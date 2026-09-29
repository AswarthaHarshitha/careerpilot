from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow

EVIDENCE_STATUSES = ("verified", "user_provided", "inferred", "unsupported")
# What a repository or entry is. Only "project" may appear in a resume's Projects section or count as evidence.
ASSET_TYPES = ("project", "personal_profile")
PROJECT_STATUSES = ("idea", "planned", "building", "built", "verified", "deployed")
# Statuses that represent finished work and may be used as resume evidence.
EVIDENCE_READY_STATUSES = ("built", "verified", "deployed")
# Evidence statuses that may appear as factual resume claims without review.
CLAIMABLE_STATUSES = ("verified", "user_provided")


class ResumeTemplate(Base):
    __tablename__ = "resume_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(20))  # latex | docx
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    sha256: Mapped[str] = mapped_column(String(64))
    source_text: Mapped[str | None] = mapped_column(Text)  # LaTeX source
    source_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)  # DOCX package
    # Parsed representation of the template (sections, entry shapes). Its shape varies per template format.
    structure: Mapped[dict] = mapped_column(JSON, default=dict)
    preview_pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    page_count: Mapped[int | None] = mapped_column(Integer)
    render_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    sections: Mapped[list["ResumeTemplateSection"]] = relationship(
        cascade="all, delete-orphan", order_by="ResumeTemplateSection.position"
    )


class ResumeTemplateSection(Base):
    __tablename__ = "resume_template_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("resume_templates.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # header|summary|experience|projects|skills|education|...
    entry_count: Mapped[int] = mapped_column(Integer, default=0)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_project_user_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    slug: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(20), default="github")  # github | manual
    description: Mapped[str] = mapped_column(Text, default="")
    repo_full_name: Mapped[str] = mapped_column(String(200), default="")
    repo_url: Mapped[str] = mapped_column(String(300), default="")
    homepage_url: Mapped[str] = mapped_column(String(300), default="")
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fork: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    primary_language: Mapped[str] = mapped_column(String(60), default="")
    languages: Mapped[dict] = mapped_column(JSON, default=dict)  # language -> bytes, as GitHub reports it
    topics: Mapped[list] = mapped_column(JSON, default=list)
    readme_excerpt: Mapped[str] = mapped_column(Text, default="")
    readme_sha: Mapped[str] = mapped_column(String(64), default="")
    # Evidence cache: structured summary of the last scan (languages, frameworks, dependencies, tests, deployment,
    # README summary, confidence). A repository is re-read only when it changed, the cache expired, or on request.
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    file_count: Mapped[int | None] = mapped_column(Integer)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    pushed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    scan_error: Mapped[str] = mapped_column(Text, default="")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # hidden from the library view
    # "personal_profile" (portfolio site, profile README) is never a project, whatever code it contains.
    asset_type: Mapped[str] = mapped_column(String(30), default="project")
    asset_type_locked: Mapped[bool] = mapped_column(Boolean, default=False)  # set by CareerPilot's portfolio rule
    # The user's switch: "Exclude from Resume Intelligence".
    resume_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="built")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    evidence: Mapped[list["ProjectEvidence"]] = relationship(
        cascade="all, delete-orphan", order_by="ProjectEvidence.id"
    )
    bullets: Mapped[list["ProjectBullet"]] = relationship(
        cascade="all, delete-orphan", order_by="ProjectBullet.sort_order"
    )

    @property
    def title(self) -> str:
        return self.display_name or self.name

    @property
    def evidence_eligible(self) -> bool:
        """True only for finished projects the user has not excluded - never for a portfolio/profile."""
        return (
            self.asset_type == "project"
            and self.resume_eligible
            and not self.hidden
            and self.status in EVIDENCE_READY_STATUSES
        )


class ProjectEvidence(Base):
    __tablename__ = "project_evidence"
    __table_args__ = (Index("ix_evidence_project_canonical", "project_id", "canonical"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # technology | concept | feature | deployment | testing | architecture
    kind: Mapped[str] = mapped_column(String(30))
    label: Mapped[str] = mapped_column(String(200))
    canonical: Mapped[str] = mapped_column(String(100), default="")  # taxonomy key when it is a known skill
    category: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20))  # verified | user_provided | inferred | unsupported
    source_type: Mapped[str] = mapped_column(
        String(30)
    )  # dependency | readme | file_tree | language | metadata | user | llm
    source_detail: Mapped[str] = mapped_column(String(300), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    user_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProjectBullet(Base):
    """Resume bullets for a project, as the user wrote or approved them."""

    __tablename__ = "project_bullets"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="user_provided")  # user_provided | template_import
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class SuggestedProject(Base):
    """A project the user could build to close a gap. Never presented as completed work."""

    __tablename__ = "suggested_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    gap_skills: Mapped[list] = mapped_column(JSON, default=list)
    existing_skills: Mapped[list] = mapped_column(JSON, default=list)
    # idea | planned | building | built | verified | deployed
    status: Mapped[str] = mapped_column(String(20), default="idea")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    linked_project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    verification_notes: Mapped[str] = mapped_column(Text, default="")
    # Per-skill verification of the linked repository: [{"skill", "label", "verified": bool, "source"}]
    verification: Mapped[list] = mapped_column(JSON, default=list)
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
