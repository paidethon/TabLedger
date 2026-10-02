"""Database models.

Amounts are stored as integer cents; timestamps as ISO strings in the bill
timezone (UTC+8) to preserve parser output exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ClassificationRule(Base):
    __tablename__ = "classification_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_key: Mapped[str] = mapped_column(String(256), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), default="")  # "" | 支出 | 收入
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subcategory: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tags: Mapped[str] = mapped_column(String(256), default="")
    origin: Mapped[str] = mapped_column(String(32), default="user")  # user | ai | import
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


Index("ix_classification_rules_key", ClassificationRule.match_key)


class AccountMapping(Base):
    __tablename__ = "account_mappings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # bank source name
    tail: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    yimu_account: Mapped[str] = mapped_column(String(64), default="")
    id_prefix: Mapped[str] = mapped_column(String(32), default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    # queued | extracting | reconciling | classifying | needs_review | exporting | done | failed | interrupted
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class JobFile(Base):
    __tablename__ = "job_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    # pending | needs_password | parsed | failed | deleted
    detected_source: Mapped[str] = mapped_column(String(32), default="")
    password_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(8), default="info")
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    record_uid: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_id: Mapped[str] = mapped_column(String(256), default="")
    datetime: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    flow: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    signed_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    net_amount_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="")
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    disposition_reason: Mapped[str] = mapped_column(Text, default="")
    merchant: Mapped[str] = mapped_column(String(256), default="", index=True)
    item: Mapped[str] = mapped_column(String(256), default="")
    account: Mapped[str] = mapped_column(String(64), default="")
    payment_method: Mapped[str] = mapped_column(String(128), default="")
    raw_type: Mapped[str] = mapped_column(String(64), default="")
    refund_original_uid: Mapped[str | None] = mapped_column(String(300), nullable=True)
    refund_uids: Mapped[list] = mapped_column(JSON, default=list)
    refund_total_cents: Mapped[int] = mapped_column(Integer, default=0)
    matched_uids: Mapped[list] = mapped_column(JSON, default=list)
    match_ids: Mapped[list] = mapped_column(JSON, default=list)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(64), default="")
    subcategory: Mapped[str] = mapped_column(String(64), default="")
    tags: Mapped[str] = mapped_column(String(256), default="")
    basis: Mapped[str] = mapped_column(String(64), default="")
    classification_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending | rule | cache | ai | manual
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    import_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "record_uid", name="uq_tx_job_uid"),
        Index("ix_tx_job_datetime", "job_id", "datetime"),
        Index("ix_tx_job_disposition", "job_id", "disposition"),
    )


class Match(Base):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    match_id: Mapped[str] = mapped_column(String(16), nullable=False)
    match_type: Mapped[str] = mapped_column(String(48), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    platform_uids: Mapped[list] = mapped_column(JSON, default=list)
    bank_uids: Mapped[list] = mapped_column(JSON, default=list)
    original_uids: Mapped[list] = mapped_column(JSON, default=list)
    refund_uids: Mapped[list] = mapped_column(JSON, default=list)
    account: Mapped[str] = mapped_column(String(64), default="")
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_delta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AIClassificationCache(Base):
    __tablename__ = "ai_classification_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    merchant: Mapped[str] = mapped_column(String(256), default="")
    item: Mapped[str] = mapped_column(String(256), default="")
    flow: Mapped[str] = mapped_column(String(8), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    subcategory: Mapped[str] = mapped_column(String(64), default="")
    tags: Mapped[str] = mapped_column(String(256), default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    requests: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class RefundOverride(Base):
    __tablename__ = "refund_overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    refund_uid: Mapped[str] = mapped_column(String(300), nullable=False)
    original_uid: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("job_id", "refund_uid", name="uq_refund_override"),)
