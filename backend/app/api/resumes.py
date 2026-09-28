import re
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

router = APIRouter(prefix="/api", tags=["resumes"])


def version_out(v: ResumeVersion, job: Job | None = None, full: bool = False) -> dict:
    out = {
        "id": v.id, "label": v.label, "job_id": v.job_id, "template_id": v.template_id, "version_number":
        v.version_number, "is_base": v.is_base, "status": v.status, "page_count": v.page_count,
        "has_pdf": v.pdf is not None, "has_source": bool(v.source_text or v.source_bytes),
        "source_format": "docx" if v.source_bytes else ("tex" if v.source_text else None),
        "render_error": v.render_error, "used_llm": v.used_llm, "created_at": v.created_at.isoformat(),
        "approved_at": v.approved_at.isoformat() if v.approved_at else None, "parent_id": v.parent_id,
        "summary": {"ats": (v.ats_report or {}).get("summary", {}), "recruiter": (v.recruiter_report or {})
                    .get("summary", {})},
    }
    if job is not None:
        out["job"] = {"id": job.id, "company": job.company, "role_title": job.role_title}
    if full:
        out.update({"content": v.content, "changes": v.changes, "ats_report": v.ats_report,
                    "recruiter_report": v.recruiter_report})
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
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.user_id == user.id,
                                                          Job.id.in_({v.job_id for v in rows if v.job_id})))}
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
    job = db.get(Job, v.job_id) if v.job_id else None
    parts = [job.company, job.role_title, f"v{v.version_number}"] if job else [v.label]
    name = "_".join(re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") for p in parts if p)[:120] or "resume"
    return f"{name}.{ext}"


@router.get("/resumes/{vid}/pdf")
def download_pdf(vid: int, download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    if v.pdf is None:
        raise AppError(v.render_error or "This version has no PDF.", code="no_pdf", status_code=404)
    disp = "attachment" if download else "inline"
    return Response(v.pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="{_filename(v, db, "pdf")}"'})


@router.get("/resumes/{vid}/source")
def download_source(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    if v.source_bytes:
        return Response(v.source_bytes,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{_filename(v, db, "docx")}"'})
    if v.source_text:
        return Response(v.source_text, media_type="application/x-tex",
                        headers={"Content-Disposition": f'attachment; filename="{_filename(v, db, "tex")}"'})
    raise AppError("This version has no source file.", code="no_source", status_code=404)


class ContentIn(BaseModel):
    content: dict


@router.patch("/resumes/{vid}")
def edit_version(vid: int, body: ContentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.update_content(db, svc.get_version(db, user.id, vid), body.content)
    return version_out(v, full=True)


class SectionIn(BaseModel):
    section_index: int = Field(ge=0, le=50)


@router.post("/resumes/{vid}/regenerate-section")
def regenerate_section(vid: int, body: SectionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.regenerate_section(db, svc.get_version(db, user.id, vid), body.section_index)
    return version_out(v, full=True)


@router.post("/resumes/{vid}/duplicate")
def duplicate(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return version_out(svc.duplicate_version(db, svc.get_version(db, user.id, vid)))


@router.post("/resumes/{vid}/approve")
def approve(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = svc.get_version(db, user.id, vid)
    if v.pdf is None:
        raise AppError("This version has no PDF yet, so it can't be approved.", code="no_pdf")
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
    used = db.scalar(select(Application.id).where(Application.resume_version_id == v.id,
                                                  Application.status.in_(("submitted", "assessment", "interview",
                                                                          "offer", "rejected"))))
    if used:
        raise AppError("This version was submitted with an application, so it is kept for your records.",
                       code="version_submitted", status_code=409)
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
    return [version_out(v) for v in db.scalars(select(ResumeVersion).where(ResumeVersion.template_id == tid,
                                                                           ResumeVersion.user_id == user.id))]
