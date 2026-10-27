from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import AppError
from app.models import Application, Job, ResumeTemplate, ResumeVersion, User
from app.services import resumes as svc
from app.services.naming import resume_filename

router = APIRouter(prefix="/api", tags=["resumes"])


def version_out(v: ResumeVersion, job: Job | None = None, full: bool = False) -> dict:
    out = {
        "id": v.id,
        "label": v.label,
        "job_id": v.job_id,
        "template_id": v.template_id,
        "version_number": v.version_number,
        "is_base": v.is_base,
        "status": v.status,
        "page_count": v.page_count,
        "has_pdf": v.pdf is not None,
        "has_source": bool(v.source_text or v.source_bytes),
        "source_format": "docx" if v.source_bytes else ("tex" if v.source_text else None),
        "render_error": v.render_error,
        "used_llm": v.used_llm,
        "created_at": v.created_at.isoformat(),
        "approved_at": v.approved_at.isoformat() if v.approved_at else None,
        "parent_id": v.parent_id,
        "summary": {
            "ats": (v.ats_report or {}).get("summary", {}),
            "recruiter": (v.recruiter_report or {}).get("summary", {}),
        },
        "export_blocked": v.export_blocked,
        "quality_counts": (v.quality_report or {}).get("counts", {}),
    }
    if job is not None:
        out["job"] = {"id": job.id, "company": job.company, "role_title": job.role_title}
    if full:
        out.update(
            {
                "content": v.content,
                "changes": v.changes,
                "ats_report": v.ats_report,
                "recruiter_report": v.recruiter_report,
                "quality_report": v.quality_report,
                "config": v.config,
            }
        )
    return out


@router.post("/jobs/{job_id}/resumes")
def generate(job_id: int, body: dict | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    template_id = (body or {}).get("template_id")
    task_id = svc.start_generation(db, user.id, job_id, int(template_id) if template_id else None)
    return {"task_id": task_id}


@router.get("/resumes")
def list_versions(job_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(ResumeVersion).where(ResumeVersion.user_id == user.id)
    if job_id:
        q = q.where(ResumeVersion.job_id == job_id)
    rows = db.scalars(q.order_by(ResumeVersion.created_at.desc()).limit(300)).all()
    jobs = {
        j.id: j
        for j in db.scalars(select(Job).where(Job.user_id == user.id, Job.id.in_({v.job_id for v in rows if v.job_id})))
    }
    return [version_out(v, jobs.get(v.job_id)) for v in rows]


@router.get("/resumes/compare")
def compare(a: int, b: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    va, vb = svc.get_version(db, user.id, a), svc.get_version(db, user.id, b)
    return {"a": version_out(va), "b": version_out(vb), "differences": svc.compare_versions(va, vb)}


@router.get("/resumes/{vid}")
def get_version(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    job = db.get(Job, v.job_id) if v.job_id else None
    return version_out(v, job, full=True)


def _filename(v: ResumeVersion, db: Session, ext: str) -> str:
    return resume_filename(db, v, ext)


@router.get("/resumes/{vid}/pdf")
def download_pdf(vid: int, download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    if v.pdf is None:
        raise AppError(v.render_error or "This version has no PDF.", code="no_pdf", status_code=404)
    if download:
        _require_export(v)
    disp = "attachment" if download else "inline"
    return Response(
        v.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disp}; filename="{_filename(v, db, "pdf")}"'},
    )


@router.get("/resumes/{vid}/source")
def download_source(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    _require_export(v)
    if v.source_bytes:
        return Response(
            v.source_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{_filename(v, db, "docx")}"'},
        )
    if v.source_text:
        return Response(
            v.source_text,
            media_type="application/x-tex",
            headers={"Content-Disposition": f'attachment; filename="{_filename(v, db, "tex")}"'},
        )
    raise AppError("This version has no source file.", code="no_source", status_code=404)


def _require_export(v: ResumeVersion) -> None:
    if v.export_blocked:
        failing = [c["label"] for c in (v.quality_report or {}).get("checks", []) if c["status"] == "fail"]
        raise AppError(
            "This resume can't be exported until its checks pass.",
            code="export_blocked",
            status_code=409,
            hint="Failing: " + "; ".join(failing) if failing else "",
        )


class LengthIn(BaseModel):
    option: str = Field(max_length=40)


@router.post("/resumes/{vid}/resolve-length")
def resolve_length(vid: int, body: LengthIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return version_out(svc.resolve_length(db, svc.get_version(db, user.id, vid), body.option), full=True)


@router.post("/resumes/{vid}/duplicate")
def duplicate(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return version_out(svc.duplicate_version(db, svc.get_version(db, user.id, vid)))


@router.post("/resumes/{vid}/approve")
def approve(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    if v.pdf is None:
        raise AppError("This version has no PDF yet, so it can't be approved.", code="no_pdf")
    _require_export(v)
    v.status = "approved"
    v.approved_at = datetime.now(UTC)
    if v.job_id:
        a = db.scalar(select(Application).where(Application.job_id == v.job_id, Application.user_id == user.id))
        if a:
            a.resume_version_id = v.id
            if a.status in ("saved", "resume_ready"):
                from app.services.applications import set_status

                set_status(db, a, "ready_to_apply", "Resume approved - ready to apply")
    db.commit()
    return version_out(v)


@router.delete("/resumes/{vid}")
def delete_version(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    if v.is_base:
        raise AppError("The original resume can't be deleted; delete its template instead.", code="base_version")
    used = db.scalar(
        select(Application.id).where(
            Application.resume_version_id == v.id,
            Application.status.in_(("submitted", "assessment", "interview", "offer", "rejected")),
        )
    )
    if used:
        raise AppError(
            "This version was submitted with an application, so it is kept for your records.",
            code="version_submitted",
            status_code=409,
        )
    for a in db.scalars(select(Application).where(Application.resume_version_id == v.id)):
        a.resume_version_id = None
    db.delete(v)
    db.commit()
    return {"ok": True}


@router.get("/templates/{tid}/versions")
def template_versions(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(ResumeTemplate, tid)
    if t is None or t.user_id != user.id:
        return []
    return [
        version_out(v)
        for v in db.scalars(
            select(ResumeVersion).where(ResumeVersion.template_id == tid, ResumeVersion.user_id == user.id)
        )
    ]


# --- live editor ----------------------------------------------------------------------------------


class OpsIn(BaseModel):
    ops: list[dict] = Field(min_length=1, max_length=100)
    base_revision: int | None = None
    label: str = Field(default="Edited", max_length=200)


class RevIn(BaseModel):
    base_revision: int | None = None


class RestoreIn(BaseModel):
    number: int = Field(ge=1)
    base_revision: int | None = None


class AssistantIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    selection: list[str] = Field(default_factory=list, max_length=20)
    base_revision: int | None = None
    apply: bool = False
    ops: list[dict] | None = None  # apply a previously previewed plan exactly as shown


@router.get("/resumes/{vid}/editor")
def editor_state(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import editor

    return editor.payload(db, svc.get_version(db, user.id, vid))


@router.post("/resumes/{vid}/ops")
def editor_ops(vid: int, body: OpsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import editor
    from app.services.resume_agent import remember_manual

    v, changed = editor.apply(
        db,
        svc.get_version(db, user.id, vid),
        body.ops,
        source="user",
        label=body.label,
        base_revision=body.base_revision,
    )
    remember_manual(v, changed)
    db.commit()
    return {**editor.payload(db, v), "changed": changed}


@router.post("/resumes/{vid}/undo")
def editor_undo(vid: int, body: RevIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import editor

    return editor.payload(db, editor.undo(db, svc.get_version(db, user.id, vid), body.base_revision))


@router.post("/resumes/{vid}/redo")
def editor_redo(vid: int, body: RevIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import editor

    return editor.payload(db, editor.redo(db, svc.get_version(db, user.id, vid), body.base_revision))


@router.post("/resumes/{vid}/restore")
def editor_restore(vid: int, body: RestoreIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import editor

    return editor.payload(db, editor.restore(db, svc.get_version(db, user.id, vid), body.number, body.base_revision))


@router.post("/resumes/{vid}/assistant")
def editor_assistant(vid: int, body: AssistantIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The resume agent: understands the request against the current document, applies targeted edits, verifies
    the result and reports what changed. Very large changes come back as a preview; the confirmed plan is sent
    back as `ops` and applied exactly as shown."""
    from app.llm.provider import get_provider
    from app.models import UserSettings
    from app.services import editor, resume_agent

    v = svc.get_version(db, user.id, vid)
    editor.ensure_history(db, v)
    if body.ops is not None:
        v, changed = editor.apply(
            db, v, body.ops, source="assistant", label=body.instruction[:200], base_revision=body.base_revision
        )
        return {"applied": True, "state": editor.payload(db, v), "changed": changed, "report": "Applied.", "steps": []}
    provider = get_provider(db.get(UserSettings, user.id))
    return resume_agent.run(db, v, body.instruction, body.selection, provider, body.base_revision, confirm=body.apply)


@router.get("/resumes/{vid}/preview/{page}.png")
def preview_page(
    vid: int, page: int, rev: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """The rendered PDF page as an image: the real output, viewable on any device."""
    from app.resume.pdf_template import render_page_png

    v = svc.get_version(db, user.id, vid)
    if v.pdf is None:
        raise AppError(v.render_error or "No PDF for this version.", code="no_pdf", status_code=404)
    if page < 1 or page > (v.page_count or 1):
        raise AppError("No such page.", code="no_page", status_code=404)
    png = render_page_png(v.pdf, page - 1, zoom=2.0)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
