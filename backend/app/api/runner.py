"""Endpoints used by the local apply agent (the CareerPilot Runner).

Authentication: the long-lived runner token is exchanged at /connect for a short-lived access token, which
every other call uses. Requests carrying a browser Origin are refused. Every instruction payload is signed
with a key derived from the runner token; the runner verifies it before acting.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_runner, runner_from_token
from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import encode_runner_access, sign_task
from app.models import AUTOMATION_TERMINAL, Application, Job, ResumeVersion
from app.services import applications as svc

router = APIRouter(prefix="/api/runner", tags=["runner"])


def instructions(db: Session, s, token) -> dict:
    a = db.get(Application, s.application_id)
    job = db.get(Job, a.job_id)
    rv = db.get(ResumeVersion, s.resume_version_id) if s.resume_version_id else None
    payload = {
        "session_id": s.id,
        "user_id": s.user_id,
        "state": s.state,
        "start_url": s.start_url,
        "platform": s.platform,
        "company": job.company if job else "",
        "role_title": job.role_title if job else "",
        "resume_version": rv.version_number if rv else None,
        "answers_revision": s.answers_revision,
        "resume_nonce": s.resume_nonce,
        "fill": svc.fill_plan(db, s),
        "submit": s.state == "submit_confirmed",
        "cancelled": s.state == "cancelled",
        "terminal": s.state in AUTOMATION_TERMINAL,
        "unresolved": [f.label for f in svc.unresolved(a)],
        "issued_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return {**payload, "signature": sign_task(token.token_hash, payload)}


class ConnectIn(BaseModel):
    host: str = Field(default="", max_length=120)
    version: str = Field(default="", max_length=40)


@router.post("/connect")
def connect(body: ConnectIn, runner=Depends(runner_from_token), db: Session = Depends(get_db)):
    """Exchanges the long-lived runner token for a short-lived access token."""
    user, token = runner
    access, expires = encode_runner_access(token.id, user.id)
    token.runner_host, token.runner_version = body.host.strip()[:120], body.version.strip()[:40]
    token.runner_state, token.runner_detail = "idle", "Connected"
    db.commit()
    return {"access_token": access, "expires_at": expires.isoformat(), "user": user.email, "token": token.name}


@router.get("/ping")
def ping(runner=Depends(current_runner)):
    user, token = runner
    return {"ok": True, "user": user.email, "token": token.name}


class HeartbeatIn(BaseModel):
    state: str = Field(default="idle", pattern="^(idle|working|waiting|blocked)$")
    detail: str = Field(default="", max_length=300)


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    token.runner_state, token.runner_detail = body.state, body.detail
    db.commit()
    return {"ok": True}


@router.post("/claim")
def claim(runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.claim_session(db, user.id, token.id)
    if s is None:
        if token.runner_state != "idle":
            token.runner_state, token.runner_detail = "idle", "Ready"
            db.commit()
        return {"session": None}
    token.runner_state, token.runner_detail = "working", f"Session {s.id}"
    db.commit()
    return {"session": instructions(db, s, token)}


@router.get("/sessions/{sid}")
def get_instructions(sid: int, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    s.last_heartbeat_at = datetime.now(UTC)
    db.commit()
    return instructions(db, s, token)


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
    return instructions(db, s, token)


class EventIn(BaseModel):
    phase: str = Field(default="", max_length=30)
    message: str = Field(max_length=300)


@router.post("/sessions/{sid}/events")
def post_event(sid: int, body: EventIn, runner=Depends(current_runner), db: Session = Depends(get_db)):
    """A step in the live timeline (no state change)."""
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    svc.record_event(db, s, body.phase, body.message)
    token.runner_detail = body.message[:300]
    db.commit()
    return {"ok": True}


class ReportIn(BaseModel):
    state: str
    phase: str = Field(default="", max_length=30)
    message: str = Field(default="", max_length=1000)
    current_url: str = Field(default="", max_length=1000)
    step: int | None = None
    filled_revision: int | None = None
    fill_results: list[dict] = Field(default_factory=list, max_length=200)
    screenshot_b64: str | None = None
    result_text: str = Field(default="", max_length=2000)
    confirmation_id: str = Field(default="", max_length=120)
    platform: str = Field(default="", max_length=40)
    resume_uploaded: bool | None = None


@router.post("/sessions/{sid}/report")
def report(sid: int, body: ReportIn, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    if s.state in AUTOMATION_TERMINAL:
        return instructions(db, s, token)
    svc.apply_report(db, s, body.model_dump())
    token.runner_state = {
        "action_required": "waiting",
        "awaiting_answers": "waiting",
        "awaiting_review": "waiting",
        "unsupported": "blocked",
        "failed": "blocked",
    }.get(s.state, "working")
    token.runner_detail = (s.message or "")[:300]
    db.commit()
    return instructions(db, s, token)


@router.get("/sessions/{sid}/resume.pdf")
def resume_pdf(sid: int, runner=Depends(current_runner), db: Session = Depends(get_db)):
    user, token = runner
    s = svc.get_runner_session(db, user.id, token.id, sid)
    rv = db.get(ResumeVersion, s.resume_version_id) if s.resume_version_id else None
    if rv is None or rv.user_id != user.id or rv.pdf is None:
        raise AppError("No resume PDF for this session.", code="no_pdf", status_code=404)
    if rv.export_blocked:
        raise AppError("This resume has failing checks and can't be uploaded.", code="export_blocked", status_code=409)
    from app.services.naming import resume_filename

    fname = resume_filename(db, rv, "pdf")
    return Response(
        rv.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Resume-Version": str(rv.version_number)},
    )
