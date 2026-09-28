"""Endpoints used by the local apply agent (authenticated with a runner token, not a browser session)."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_runner
from app.core.db import get_db
from app.core.errors import AppError
from app.models import AUTOMATION_TERMINAL, Application, Job, ResumeVersion
from app.services import applications as svc

router = APIRouter(prefix="/api/runner", tags=["runner"])


def instructions(db: Session, s) -> dict:
    a = db.get(Application, s.application_id)
    job = db.get(Job, a.job_id)
    return {
        "session_id": s.id, "state": s.state, "start_url": s.start_url, "platform": s.platform,
        "company": job.company if job else "", "role_title": job.role_title if job else "",
        "answers_revision": s.answers_revision, "fill": svc.fill_plan(db, s),
        "submit": s.state == "submit_confirmed", "cancelled": s.state == "cancelled",
        "terminal": s.state in AUTOMATION_TERMINAL,
        "unresolved": [f.label for f in svc.unresolved(a)],
    }


@router.get("/ping")
def ping(runner=Depends(current_runner)):
    user, token = runner
    return {"ok": True, "user": user.email, "token": token.name}


@router.post("/claim")
def claim(runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.claim_session(db, user.id, token.id)
    if s is None:
        return {"session": None}
    return {"session": instructions(db, s)}


@router.get("/sessions/{sid}")
def get_instructions(sid: int, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    return instructions(db, s)


class FieldsIn(BaseModel):
    step: int = 0
    fields: list[dict] = Field(max_length=200)


@router.post("/sessions/{sid}/fields")
def post_fields(sid: int, body: FieldsIn, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    if s.state in AUTOMATION_TERMINAL:
        raise AppError("This session has ended.", code="session_ended", status_code=409)
    s.step = body.step
    svc.ingest_fields(db, s, [dict(f, step=body.step) for f in body.fields])
    return instructions(db, s)


class ReportIn(BaseModel):
    state: str
    message: str = Field(default="", max_length=1000)
    current_url: str = Field(default="", max_length=1000)
    step: int | None = None
    filled_revision: int | None = None
    fill_results: list[dict] = Field(default_factory=list, max_length=200)
    screenshot_b64: str | None = None
    result_text: str = Field(default="", max_length=2000)


@router.post("/sessions/{sid}/report")
def report(sid: int, body: ReportIn, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    if s.state in AUTOMATION_TERMINAL:
        return instructions(db, s)
    svc.apply_report(db, s, body.model_dump())
    return instructions(db, s)


@router.get("/sessions/{sid}/resume.pdf")
def resume_pdf(sid: int, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    rv = db.get(ResumeVersion, s.resume_version_id) if s.resume_version_id else None
    if rv is None or rv.user_id != user.id or rv.pdf is None:
        raise AppError("No resume PDF for this session.", code="no_pdf", status_code=404)
    job = db.get(Job, db.get(Application, s.application_id).job_id)
    import re

    who = re.sub(r"[^A-Za-z0-9]+", "_", (job.company if job else "") or "Resume").strip("_")
    return Response(rv.pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="Resume_{who}.pdf"'})
