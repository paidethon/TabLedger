"""FastAPI auth dependencies: session cookie, CSRF protection, client IP."""

from __future__ import annotations

import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DBSession

from app.auth import sessions as session_svc
from app.config import get_settings
from app.db.database import get_session_factory
from app.db.models import User


def db_dep():
    """Yield a database session per request."""

    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


DB = Annotated[DBSession, Depends(db_dep)]


def client_ip(request: Request) -> str:
    """Client IP: only trust X-Forwarded-For from configured local proxies."""

    settings = get_settings()
    trusted = {h.strip() for h in settings.trusted_proxies.split(",") if h.strip()}
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if peer in trusted and forwarded:
        first = forwarded.split(",")[0].strip()
        try:
            ipaddress.ip_address(first)
            return first
        except ValueError:
            return peer
    return peer


def get_session_token(request: Request) -> str:
    return request.cookies.get(get_settings().cookie_name, "")


def get_csrf_cookie(request: Request) -> str:
    return request.cookies.get(get_settings().csrf_cookie_name, "")


def current_user(request: Request, db: DB) -> User:
    token = get_session_token(request)
    session_row = session_svc.get_valid_session(db, token)
    if session_row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或会话已过期")
    user = db.get(User, session_row.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    request.state.session_row = session_row
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_csrf(request: Request, db: DB) -> None:
    """Double-submit CSRF check for state-changing requests.

    The CSRF cookie is set at login (readable by JS); mutating requests must
    echo it in the X-CSRF-Token header.  SameSite=Strict is the first line of
    defence; this header check is the second.
    """

    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    cookie_token = get_csrf_cookie(request)
    header_token = request.headers.get("x-csrf-token", "")
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")


CSRF = Depends(require_csrf)
