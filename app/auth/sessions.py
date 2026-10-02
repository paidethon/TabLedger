"""Server-side opaque sessions and login rate limiting.

Session tokens are 256-bit random values; only their SHA-256 digest is
stored.  Token lookups fetch candidate rows with static, fully-bound
SQLAlchemy queries and compare digests in Python with
``hmac.compare_digest`` — caller-supplied values never become part of a
SQL statement in any form, and lookups stay constant-time.  Maintenance
purges delete rows one by one through the ORM.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.db.models import LoginAttempt, Session, User, utcnow


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_ip(ip: str) -> str:
    secret = get_settings().ensure_secret_key()
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()


def create_session(db: DBSession, user_id: int, ip: str = "", user_agent: str = "") -> tuple[str, Session]:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    token_digest = hash_token(token)
    now = utcnow()
    row = Session(
        token_hash=token_digest,
        user_id=user_id,
        created_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        ip_hash=hash_ip(ip) if ip else "",
        user_agent=user_agent[:256],
    )
    db.add(row)
    db.flush()
    return token, row


def get_valid_session(db: DBSession, token: str) -> Session | None:
    if not token:
        return None
    token_digest = hash_token(token)
    now = utcnow()
    # Static query only (no caller-derived values); digest compared in Python.
    candidates = db.scalars(
        select(Session).where(Session.revoked_at.is_(None), Session.expires_at > now)
    )
    for row in candidates:
        if hmac.compare_digest(row.token_hash, token_digest):
            return row
    return None


def revoke_session(db: DBSession, token: str) -> None:
    token_digest = hash_token(token)
    candidates = db.scalars(select(Session).where(Session.revoked_at.is_(None)))
    for row in candidates:
        if hmac.compare_digest(row.token_hash, token_digest):
            row.revoked_at = utcnow()
            return


def revoke_all_user_sessions(db: DBSession, user_id: int, *, except_token: str | None = None) -> None:
    except_digest = hash_token(except_token) if except_token else None
    for row in db.scalars(select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))):
        if except_digest is not None and hmac.compare_digest(row.token_hash, except_digest):
            continue
        row.revoked_at = utcnow()


def purge_expired_sessions(db: DBSession) -> None:
    cutoff = utcnow() - timedelta(days=1)
    stale = db.scalars(select(Session).where(Session.expires_at < cutoff))
    for row in stale:
        db.delete(row)


# ---------------------------------------------------------------------------
# Login rate limiting
# ---------------------------------------------------------------------------

WINDOW_SECONDS = 900
MAX_FAILURES_WINDOW = 5
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600


def _recent_failures(db: DBSession) -> list[LoginAttempt]:
    window_start = utcnow() - timedelta(seconds=WINDOW_SECONDS)
    return list(
        db.scalars(
            select(LoginAttempt).where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= window_start)
        )
    )


def backoff_seconds(db: DBSession, username: str, ip: str) -> int:
    """Exponential backoff from failures on either the account or the IP."""

    failures_for_user = 0
    failures_for_ip = 0
    ip_key = hash_ip(ip)
    for attempt in _recent_failures(db):
        if hmac.compare_digest(attempt.username, username.lower()):
            failures_for_user += 1
        if attempt.ip_hash and hmac.compare_digest(attempt.ip_hash, ip_key):
            failures_for_ip += 1
    failures = max(failures_for_user, failures_for_ip)
    if failures < MAX_FAILURES_WINDOW:
        return 0
    exponent = failures - MAX_FAILURES_WINDOW
    return min(BASE_BACKOFF_SECONDS * (2**exponent), MAX_BACKOFF_SECONDS)


def record_attempt(db: DBSession, username: str, ip: str, success: bool) -> None:
    db.add(
        LoginAttempt(
            username=username.lower(),
            ip_hash=hash_ip(ip) if ip else "",
            success=success,
            created_at=utcnow(),
        )
    )
    purge_old_attempts(db)


def purge_old_attempts(db: DBSession) -> None:
    cutoff = utcnow() - timedelta(days=7)
    stale = db.scalars(select(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    for row in stale:
        db.delete(row)


def session_expiry() -> datetime:
    return utcnow() + timedelta(hours=get_settings().session_ttl_hours)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
