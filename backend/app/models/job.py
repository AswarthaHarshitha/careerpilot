from datetime import date, datetime

from sqlalchemy import JSON, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    role_title: Mapped[str] = mapped_column(String(300), default="")
    job_code: Mapped[str] = mapped_column(String(120), default="")  # the employer's job / requisition id
    job_url: Mapped[str] = mapped_column(String(1000), default="")
    application_url: Mapped[str] = mapped_column(String(1000), default="")
    company_careers_url: Mapped[str] = mapped_column(String(1000), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    workplace_type: Mapped[str] = mapped_column(String(40), default="")  # onsite | hybrid | remote
    employment_type: Mapped[str] = mapped_column(String(60), default="")
    experience_text: Mapped[str] = mapped_column(String(300), default="")
    min_years_experience: Mapped[float | None] = mapped_column(Float)
    education_text: Mapped[str] = mapped_column(String(500), default="")
    # Only set when the posting states it. deadline_source quotes where it came from.
    deadline: Mapped[date | None] = mapped_column(Date)
    deadline_source: Mapped[str] = mapped_column(String(300), default="")
    posted_date: Mapped[date | None] = mapped_column(Date)
    source_platform: Mapped[str] = mapped_column(String(40), default="")
    ingest_method: Mapped[str] = mapped_column(String(20), default="url")  # url | paste | pdf | docx | text
    description_text: Mapped[str] = mapped_column(Text, default="")
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    qualifications: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    analysis_state: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|ready|failed
    analysis_error: Mapped[str] = mapped_column(Text, default="")
    analysis_notes: Mapped[list] = mapped_column(JSON, default=list)
    analyzed_with_llm: Mapped[bool] = mapped_column(default=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    archived: Mapped[bool] = mapped_column(default=False)
    # Job-specific overrides. They apply to this job's resumes only and never change the master profile.
    # {"controls": {...}, "custom": [text, ...]} - see app.resume.instructions
    resume_instructions: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")  # "My Notes & Ideas"
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    # Company research (facts cite their source page) and interview preparation (labelled as suggestions).
    company_website: Mapped[str] = mapped_column(String(300), default="")
    research: Mapped[dict] = mapped_column(JSON, default=dict)
    research_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    interview_prep: Mapped[dict] = mapped_column(JSON, default=dict)
    interview_prep_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    requirements: Mapped[list["JobRequirement"]] = relationship(
        cascade="all, delete-orphan", order_by="JobRequirement.position"
    )
    project_recommendations: Mapped[list["JobProjectRecommendation"]] = relationship(
        cascade="all, delete-orphan", order_by="JobProjectRecommendation.rank"
    )


class JobRequirement(Base):
    __tablename__ = "job_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # skill | experience | education | certification | domain | other
    kind: Mapped[str] = mapped_column(String(30))
    label: Mapped[str] = mapped_column(String(300))
    canonical: Mapped[str] = mapped_column(String(100), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    importance: Mapped[str] = mapped_column(String(20))  # required | preferred | keyword
    source_snippet: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    match: Mapped["JobMatch | None"] = relationship(
        back_populates="requirement", cascade="all, delete-orphan", uselist=False
    )


class JobMatch(Base):
    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("requirement_id", name="uq_match_requirement"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("job_requirements.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30))  # strong | partial | missing | needs_verification
    rationale: Mapped[str] = mapped_column(Text, default="")
    # References to the evidence rows that support the status: [{type, id, label, status}]
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    requirement: Mapped[JobRequirement] = relationship(back_populates="match")


class JobProjectRecommendation(Base):
    __tablename__ = "job_project_recommendations"
    __table_args__ = (UniqueConstraint("job_id", "project_id", name="uq_job_project"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    matched: Mapped[list] = mapped_column(JSON, default=list)  # [{requirement_id, label, importance, evidence_status}]
    selected: Mapped[bool] = mapped_column(default=False)
