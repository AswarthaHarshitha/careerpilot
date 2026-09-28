"""Resume editing transactions: apply operations, re-render, validate, record history, undo/redo, restore.

Concurrency: every change names the revision it was made against. If the resume moved on in the meantime (another
tab or device), the change is refused with a conflict instead of silently overwriting newer work.
"""

import copy
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Job, ResumeRevision, ResumeTemplate, ResumeVersion, UserProfile, UserSettings
from app.resume.document import PROVENANCE_LABEL, apply_ops, ensure_ids, index, user_claims


class RevisionConflict(AppError):
    def __init__(self, current: int):
        super().__init__("This resume was changed somewhere else.", code="revision_conflict", status_code=409,
                         hint=f"The latest revision is {current}.", actions=["keep_current", "use_newer", "compare"])
        self.current = current


def _record(db: Session, v: ResumeVersion, label: str, source: str, changed: list[str]) -> None:
    # a new change discards anything that could have been redone
    db.execute(delete(ResumeRevision).where(ResumeRevision.version_id == v.id, ResumeRevision.number > v.revision))
    number = (db.scalar(select(func.max(ResumeRevision.number)).where(ResumeRevision.version_id == v.id)) or 0) + 1
    db.add(ResumeRevision(version_id=v.id, number=number, label=label[:300], source=source,
                          content=copy.deepcopy(v.content), config=copy.deepcopy(v.config or {}), changed=changed,
                          page_count=v.page_count))
    v.revision = number


def ensure_history(db: Session, v: ResumeVersion) -> None:
    """Versions created before the editor existed get their current state as revision 1."""
    ensure_ids(v.content)
    if not db.scalar(select(ResumeRevision.id).where(ResumeRevision.version_id == v.id)):
        db.add(ResumeRevision(version_id=v.id, number=1, label="Generated", source="generated",
                              content=copy.deepcopy(v.content), config=copy.deepcopy(v.config or {}), changed=[],
                              page_count=v.page_count))
        v.revision = 1
        db.flush()


def _check_editable(v: ResumeVersion, base_revision: int | None) -> None:
    if v.is_base:
        raise AppError("The original resume is kept exactly as uploaded. Generate a version for a job to edit it.",
                       code="version_locked")
    if base_revision is not None and base_revision != v.revision:
        raise RevisionConflict(v.revision)


def _rerender(db: Session, v: ResumeVersion) -> None:
    from app.services.resumes import rerender_version

    cfg = dict(v.config or {})
    if v.content.get("page_target"):
        cfg["length"] = v.content["page_target"]
        v.config = cfg
    rerender_version(db, v)


def apply(db: Session, v: ResumeVersion, ops: list[dict], *, source: str, label: str,
          base_revision: int | None) -> tuple[ResumeVersion, list[str]]:
    ensure_history(db, v)
    _check_editable(v, base_revision)
    content, changed = apply_ops(v.content, ops, source)
    v.content = content
    if v.status == "approved":
        v.status, v.approved_at = "draft", None  # an edited version needs approving again
    notes: list[str] = []
    if any(o.get("op") == "set_page_target" for o in ops):
        notes = _fit(db, v)
    _rerender(db, v)
    if notes:
        label = label + " — " + "; ".join(notes)[:200]
        changed = changed + ["fit"]
    _record(db, v, label, source, changed)
    db.commit()
    return v, changed


def _fit(db: Session, v: ResumeVersion) -> list[str]:
    """Makes the smallest reasonable reductions to meet the page target, reporting each one."""
    from app.services.resumes import fit_to_length

    tpl = db.get(ResumeTemplate, v.template_id)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == v.user_id))
    cfg = dict(v.config or {}, length=v.content.get("page_target", 1))
    changes: list[dict] = []
    content, *_ = fit_to_length(tpl, v.content, cfg, profile, changes)
    v.content = content
    v.config = cfg
    return [c["detail"] for c in changes]


def _load_revision(db: Session, v: ResumeVersion, number: int) -> ResumeRevision:
    r = db.scalar(select(ResumeRevision).where(ResumeRevision.version_id == v.id, ResumeRevision.number == number))
    if r is None:
        raise AppError("That revision does not exist.", code="unknown_revision", status_code=404)
    return r


def undo(db: Session, v: ResumeVersion, base_revision: int | None) -> ResumeVersion:
    ensure_history(db, v)
    _check_editable(v, base_revision)
    if v.revision <= 1:
        raise AppError("Nothing to undo.", code="nothing_to_undo")
    r = _load_revision(db, v, v.revision - 1)
    v.content, v.config, v.revision = copy.deepcopy(r.content), copy.deepcopy(r.config), r.number
    _rerender(db, v)
    db.commit()
    return v


def redo(db: Session, v: ResumeVersion, base_revision: int | None) -> ResumeVersion:
    ensure_history(db, v)
    _check_editable(v, base_revision)
    r = db.scalar(select(ResumeRevision).where(ResumeRevision.version_id == v.id,
                                               ResumeRevision.number == v.revision + 1))
    if r is None:
        raise AppError("Nothing to redo.", code="nothing_to_redo")
    v.content, v.config, v.revision = copy.deepcopy(r.content), copy.deepcopy(r.config), r.number
    _rerender(db, v)
    db.commit()
    return v


def restore(db: Session, v: ResumeVersion, number: int, base_revision: int | None) -> ResumeVersion:
    """Makes an earlier state current as a new change, so the history in between is kept."""
    ensure_history(db, v)
    _check_editable(v, base_revision)
    r = _load_revision(db, v, number)
    v.content, v.config = copy.deepcopy(r.content), copy.deepcopy(r.config)
    _rerender(db, v)
    db.execute(delete(ResumeRevision).where(ResumeRevision.version_id == v.id, ResumeRevision.number > v.revision))
    _record(db, v, f"Restored revision {number} ({r.label})", "restore", [])
    db.commit()
    return v


def payload(db: Session, v: ResumeVersion) -> dict:
    ensure_history(db, v)
    db.commit()
    revs = db.scalars(select(ResumeRevision).where(ResumeRevision.version_id == v.id)
                      .order_by(ResumeRevision.number.desc())).all()
    head = revs[0].number if revs else 1
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == v.user_id))
    tpl = db.get(ResumeTemplate, v.template_id) if v.template_id else None
    job = db.get(Job, v.job_id) if v.job_id else None
    settings = db.get(UserSettings, v.user_id)
    return {
        "id": v.id, "revision": v.revision, "head": head, "can_undo": v.revision > 1, "can_redo": v.revision < head,
        "content": v.content, "elements": index(v.content), "page_count": v.page_count,
        "page_target": v.content.get("page_target") or (v.config or {}).get("length") or
        (settings.resume_max_pages if settings else 1),
        "status": v.status, "export_blocked": v.export_blocked, "quality_report": v.quality_report,
        "render_error": v.render_error, "label": v.label, "template": tpl.name if tpl else None,
        "job": {"id": job.id, "company": job.company, "role_title": job.role_title} if job else None,
        "profile_links": {"portfolio": profile.portfolio_url if profile else "",
                          "github": profile.github_url if profile else "",
                          "linkedin": profile.linkedin_url if profile else ""},
        "unverified_user_claims": user_claims(v.content),
        "provenance_labels": PROVENANCE_LABEL,
        "history": [{"number": r.number, "label": r.label, "source": r.source, "changed": r.changed,
                     "page_count": r.page_count, "created_at": r.created_at.isoformat(), "current": r.number == v.revision}
                    for r in revs[:60]],
        "updated_at": datetime.now(UTC).isoformat(),
    }
