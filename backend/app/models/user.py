from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)

    settings: Mapped["UserSettings"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class AuthSession(Base):
    """Server-side record behind each login cookie, so sessions can be listed and revoked."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    csrf_token: Mapped[str] = mapped_column(String(64))


class ApiToken(Base):
    """Tokens for the local apply agent. Only a SHA-256 hash is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    default_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_templates.id", ondelete="SET NULL", use_alter=True, name="fk_settings_default_template")
    )
    resume_max_pages: Mapped[int] = mapped_column(Integer, default=1)
    max_projects: Mapped[int] = mapped_column(Integer, default=3)
    max_bullets_per_project: Mapped[int] = mapped_column(Integer, default=3)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    # "type_company": final submission requires typing the company name; "click": a single explicit click.
    # Neither mode lets an application be submitted without an explicit action by the user.
    submit_confirmation_mode: Mapped[str] = mapped_column(String(20), default="type_company")
    deadline_reminder_days: Mapped[int] = mapped_column(Integer, default=3)
    follow_up_after_days: Mapped[int] = mapped_column(Integer, default=7)

    llm_provider: Mapped[str] = mapped_column(String(32), default="server")  # server|none|gemini|openai_compatible
    llm_model: Mapped[str] = mapped_column(String(120), default="")
    llm_base_url: Mapped[str] = mapped_column(String(300), default="")
    llm_api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)

    github_username: Mapped[str] = mapped_column(String(100), default="")
    github_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    include_private_repos: Mapped[bool] = mapped_column(Boolean, default=False)
    include_forks: Mapped[bool] = mapped_column(Boolean, default=False)
    github_last_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Master defaults for resume header links: "template" keeps what the template has, or "include"/"exclude".
    header_portfolio: Mapped[str] = mapped_column(String(10), default="template")
    header_github: Mapped[str] = mapped_column(String(10), default="template")
    header_linkedin: Mapped[str] = mapped_column(String(10), default="template")
    notify_in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="settings")
