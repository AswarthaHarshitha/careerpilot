from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow


class AIUsage(Base):
    """Requests CareerPilot itself sent to each AI provider, per user and day (application-side counts)."""

    __tablename__ = "ai_usage"
    __table_args__ = (UniqueConstraint("user_id", "provider", "day", name="uq_ai_usage_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    day: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, UTC
    requests: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    rate_limited: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(400), default="")
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AIResponseCache(Base):
    """Answers to identical low-temperature requests, reused instead of calling a provider again."""

    __tablename__ = "ai_response_cache"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_ai_cache_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(20))
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
