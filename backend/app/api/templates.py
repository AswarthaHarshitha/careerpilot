from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.models import ResumeTemplate, ResumeVersion, User, UserSettings
from app.resume.latex_template import summarize_structure
from app.services.profile_import import import_from_template
from app.services.resumes import create_template

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _owned(db: Session, user_id: int, tid: int) -> ResumeTemplate:
    t = db.get(ResumeTemplate, tid)
    if t is None or t.user_id != user_id:
        raise NotFound("Template")
    return t


def template_out(t: ResumeTemplate, default_id: int | None) -> dict:
    return {
        "id": t.id, "name": t.name, "format": t.format, "original_filename": t.original_filename,
        "created_at": t.created_at.isoformat(), "page_count": t.page_count, "render_error": t.render_error,
        "has_preview": t.preview_pdf is not None, "is_default": t.id == default_id,
        "sections": summarize_structure(t.structure),
        "header": {k: v for k, v in (t.structure.get("header") or {}).items() if k != "span"},
    }


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppError(f"The file is larger than {max_bytes // 1_000_000} MB.", code="file_too_large", status_code=413)
    if not data:
        raise AppError("The file is empty.", code="empty_file")
    return data


@router.get("")
def list_templates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = db.get(UserSettings, user.id)
    rows = db.scalars(select(ResumeTemplate).where(ResumeTemplate.user_id == user.id)
                      .order_by(ResumeTemplate.created_at.desc()))
    return [template_out(t, settings.default_template_id if settings else None) for t in rows]


@router.post("")
async def upload_template(file: UploadFile = File(...), name: str = Form(default=""),
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await read_upload(file, get_settings().upload_max_bytes)
    filename = (file.filename or "resume").rsplit("/", 1)[-1][:200]
    tpl, task_id = create_template(db, user.id, filename, data, name.strip()[:200])
    settings = db.get(UserSettings, user.id)
    return {"template": template_out(tpl, settings.default_template_id), "task_id": task_id}


@router.get("/{tid}")
def get_template(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _owned(db, user.id, tid)
    settings = db.get(UserSettings, user.id)
    out = template_out(t, settings.default_template_id)
    out["structure"] = _structure_detail(t.structure)
    return out


def _structure_detail(structure: dict) -> list[dict]:
    out = []
    for s in structure.get("sections", []):
        item = {"index": s["index"], "title": s["title"], "kind": s["kind"], "layout": s["layout"]}
        if s["layout"] == "entries":
            item["entries"] = [{"fields": e["fields"], "bullets": [it["text"] for it in e["items"]]}
                               for e in s["entries"]]
        elif s["layout"] == "skills":
            item["skills"] = [{"name": c["name"], "items": c["items"]} for c in s["skills"]]
        elif s["layout"] == "items":
            item["items"] = [it["text"] for it in s["items"]]
        elif s["layout"] == "paragraph":
            item["text"] = s["paragraph"]["text"]
        out.append(item)
    return out


@router.get("/{tid}/preview.pdf")
def template_preview(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _owned(db, user.id, tid)
    if t.preview_pdf is None:
        raise AppError(t.render_error or "The preview is still being generated.", code="no_preview", status_code=404)
    return Response(t.preview_pdf, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=template-preview.pdf"})


@router.post("/{tid}/default")
def make_default(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _owned(db, user.id, tid)
    settings = db.get(UserSettings, user.id)
    settings.default_template_id = t.id
    if t.page_count and settings.resume_max_pages < t.page_count:
        settings.resume_max_pages = t.page_count
    db.commit()
    return {"ok": True}


@router.post("/{tid}/import-profile")
def import_profile(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _owned(db, user.id, tid)
    return {"imported": import_from_template(db, user.id, t)}


@router.delete("/{tid}")
def delete_template(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _owned(db, user.id, tid)
    in_use = db.scalar(select(ResumeVersion.id).where(ResumeVersion.template_id == t.id,
                                                      ResumeVersion.is_base.is_(False)).limit(1))
    if in_use:
        raise AppError("Resume versions were generated from this template. Delete those versions first, or keep the "
                       "template.", code="template_in_use", status_code=409)
    settings = db.get(UserSettings, user.id)
    if settings.default_template_id == t.id:
        settings.default_template_id = None
    for v in db.scalars(select(ResumeVersion).where(ResumeVersion.template_id == t.id)):
        db.delete(v)
    db.delete(t)
    db.commit()
    return {"ok": True}
