from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow


class UserProfile(Base):
    """The canonical career profile. Every generated resume and application answer is drawn from here."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    linkedin_url: Mapped[str] = mapped_column(String(300), default="")
    github_url: Mapped[str] = mapped_column(String(300), default="")
    portfolio_url: Mapped[str] = mapped_column(String(300), default="")
    headline: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")

    # Application preferences. None means "not provided" - never guessed.
    work_authorization: Mapped[str] = mapped_column(String(300), default="")
    requires_sponsorship: Mapped[bool | None] = mapped_column(Boolean)
    willing_to_relocate: Mapped[bool | None] = mapped_column(Boolean)
    notice_period: Mapped[str] = mapped_column(String(100), default="")
    salary_expectation: Mapped[str] = mapped_column(String(100), default="")
    years_of_experience: Mapped[float | None] = mapped_column(Numeric(4, 1))
    earliest_start_date: Mapped[str] = mapped_column(String(40), default="")
    preferred_locations: Mapped[list] = mapped_column(JSON, default=list)
    preferred_job_types: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Education(Base):
    __tablename__ = "educations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    institution: Mapped[str] = mapped_column(String(200))
    degree: Mapped[str] = mapped_column(String(200), default="")
    field_of_study: Mapped[str] = mapped_column(String(200), default="")
    start_date: Mapped[str] = mapped_column(String(40), default="")
    end_date: Mapped[str] = mapped_column(String(40), default="")
    graduation_year: Mapped[int | None] = mapped_column(Integer)
    grade: Mapped[str] = mapped_column(String(80), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Experience(Base):
    __tablename__ = "experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    employment_type: Mapped[str] = mapped_column(String(60), default="")
    start_date: Mapped[str] = mapped_column(String(40), default="")
    end_date: Mapped[str] = mapped_column(String(40), default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    bullets: Mapped[list["ExperienceBullet"]] = relationship(
        cascade="all, delete-orphan", order_by="ExperienceBullet.sort_order"
    )


class ExperienceBullet(Base):
    __tablename__ = "experience_bullets"

    id: Mapped[int] = mapped_column(primary_key=True)
    experience_id: Mapped[int] = mapped_column(ForeignKey("experiences.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("user_id", "canonical", name="uq_skill_user_canonical"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    canonical: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(80), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    issuer: Mapped[str] = mapped_column(String(200), default="")
    date: Mapped[str] = mapped_column(String(40), default="")
    url: Mapped[str] = mapped_column(String(300), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ProfileAnswer(Base):
    """Saved answers to recurring application questions (e.g. "How did you hear about us?").

    Sensitive answers (self-identification, legal status) are only ever proposed, never submitted without
    the user confirming them for the specific application.
    """

    __tablename__ = "profile_answers"
    __table_args__ = (UniqueConstraint("user_id", "question_key", name="uq_profile_answer_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question_key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(300), default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
