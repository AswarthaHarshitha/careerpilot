from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow

APPLICATION_STATUSES = (
    "saved",
    "analyzing",
    "resume_ready",
    "ready_to_apply",
    "in_progress",
    "submitted",
    "assessment",
    "interview",
    "rejected",
    "withdrawn",
    "offer",
)
ACTIVE_STATUSES = (
    "saved",
    "analyzing",
    "resume_ready",
    "ready_to_apply",
    "in_progress",
    "submitted",
    "assessment",
    "interview",
)


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    __table_args__ = (Index("ix_resume_versions_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("resume_templates.id", ondelete="SET NULL"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(300))
    is_base: Mapped[bool] = mapped_column(Boolean, default=False)  # the untouched resume from the template
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    # The structured resume document that was rendered into the template.
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    source_text: Mapped[str | None] = mapped_column(Text)
    source_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    page_count: Mapped[int | None] = mapped_column(Integer)
    render_error: Mapped[str] = mapped_column(Text, default="")
    ats_report: Mapped[dict] = mapped_column(JSON, default=dict)
    recruiter_report: Mapped[dict] = mapped_column(JSON, default=dict)
    changes: Mapped[list] = mapped_column(JSON, default=list)
    # Effective configuration (template defaults + master settings + job instructions) used for this version.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Pre-export gate: {"checks": [...], "blocked": bool, "length_conflict": {...} | None}
    quality_report: Mapped[dict] = mapped_column(JSON, default=dict)
    export_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Edit history: `revision` is the state currently shown; revisions above it can be redone.
    revision: Mapped[int] = mapped_column(Integer, default=1)
    used_llm: Mapped[bool] = mapped_column(Boolean, default=False)
    # What the editing agent last worked on, so follow-ups like "some more" or "it" resolve to the same target.
    assistant_context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ResumeRevision(Base):
    """One edit transaction on a resume: the complete document state after the change."""

    __tablename__ = "resume_revisions"
    __table_args__ = (UniqueConstraint("version_id", "number", name="uq_revision_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(20))  # generated | user | assistant | restore
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    changed: Mapped[list] = mapped_column(JSON, default=list)  # element ids touched by this change
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),
        Index("ix_applications_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30), default="saved")
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    date_applied: Mapped[date | None] = mapped_column(Date)
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    submission_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_text: Mapped[str] = mapped_column(Text, default="")
    confirmation_id: Mapped[str] = mapped_column(String(120), default="")  # application ID shown by the employer
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    fields: Mapped[list["ApplicationField"]] = relationship(
        cascade="all, delete-orphan", order_by="ApplicationField.position"
    )
    answers: Mapped[list["ApplicationAnswer"]] = relationship(cascade="all, delete-orphan")
    events: Mapped[list["ApplicationEvent"]] = relationship(
        cascade="all, delete-orphan", order_by="ApplicationEvent.created_at"
    )


class ApplicationField(Base):
    """One field found on the employer's application form, and what CareerPilot proposes to put in it."""

    __tablename__ = "application_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[int] = mapped_column(Integer, default=0)
    field_ref: Mapped[str] = mapped_column(String(300))  # stable reference the browser runner uses to locate it
    label: Mapped[str] = mapped_column(Text)
    input_type: Mapped[str] = mapped_column(String(30))  # text|email|tel|url|textarea|select|radio|checkbox|file|date
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[list] = mapped_column(JSON, default=list)
    field_key: Mapped[str] = mapped_column(String(80), default="")  # canonical meaning, e.g. first_name
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    value: Mapped[str] = mapped_column(Text, default="")
    value_source: Mapped[str] = mapped_column(String(30), default="none")  # profile|resume|saved_answer|draft|user|none
    # filled | needs_input | needs_confirmation | confirmed | skipped | unsupported
    status: Mapped[str] = mapped_column(String(30), default="needs_input")
    note: Mapped[str] = mapped_column(Text, default="")
    fill_result: Mapped[str] = mapped_column(String(30), default="")  # ok | failed | ""
    validation_message: Mapped[str] = mapped_column(Text, default="")


class ApplicationAnswer(Base):
    """Answers to free-text application questions, drafted from the profile and confirmed by the user."""

    __tablename__ = "application_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int | None] = mapped_column(ForeignKey("application_fields.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="user")  # draft | user | saved_answer
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # status | note | automation | submission | resume
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


AUTOMATION_STATES = (
    "queued",  # waiting for a runner
    "running",  # runner is navigating / inspecting
    "action_required",  # user must log in, solve a CAPTCHA, or act in the browser
    "awaiting_answers",  # form inspected; unresolved fields need the user
    "filling",
    "awaiting_review",  # everything fillable is filled; waiting for the user's decision
    "submit_confirmed",  # user explicitly confirmed; runner may click submit
    "submitting",
    "submitted",  # confirmation detected on the page
    "submitted_unverified",  # submit clicked but no reliable confirmation
    "unsupported",
    "failed",
    "cancelled",
)
AUTOMATION_TERMINAL = ("submitted", "submitted_unverified", "unsupported", "failed", "cancelled")


class AutomationSession(Base):
    __tablename__ = "automation_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    start_url: Mapped[str] = mapped_column(String(1000))
    platform: Mapped[str] = mapped_column(String(40), default="")
    runner_token_id: Mapped[int | None] = mapped_column(ForeignKey("api_tokens.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(Text, default="")
    current_url: Mapped[str] = mapped_column(String(1000), default="")
    step: Mapped[int] = mapped_column(Integer, default=0)
    screenshot_png: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Increments whenever answers change, so the runner knows to re-apply them.
    answers_revision: Mapped[int] = mapped_column(Integer, default=0)
    filled_revision: Mapped[int] = mapped_column(Integer, default=-1)
    submit_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Fine-grained step reported by the runner (OPENING_PAGE, MAPPING_FORM, UPLOADING_RESUME, ...).
    phase: Mapped[str] = mapped_column(String(30), default="")
    # Bumped when the user clicks "Continue application" after a CAPTCHA, sign-in or other hand-off.
    resume_nonce: Mapped[int] = mapped_column(Integer, default=0)
    submit_clicked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fields_total: Mapped[int] = mapped_column(Integer, default=0)
    fields_done: Mapped[int] = mapped_column(Integer, default=0)
    resume_uploaded: Mapped[bool] = mapped_column(Boolean, default=False)
    result_text: Mapped[str] = mapped_column(Text, default="")
    log: Mapped[list] = mapped_column(JSON, default=list)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_user_due", "user_id", "due_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))  # deadline | follow_up | custom
    due_date: Mapped[date] = mapped_column(Date)
    message: Mapped[str] = mapped_column(Text)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BackgroundTask(Base):
    """Database-backed job queue for long-running work (GitHub scans, job analysis, resume rendering)."""

    __tablename__ = "background_tasks"
    __table_args__ = (Index("ix_tasks_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | succeeded | failed
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{label, state: pending|running|done|failed}]
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
