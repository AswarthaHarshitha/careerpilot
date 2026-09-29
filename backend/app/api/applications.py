from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.ratelimit import client_ip
from app.models import APPLICATION_STATUSES, Application, ApplicationEvent, Job, ResumeVersion, User
from app.services import applications as svc
from app.services.jobs import days_remaining

router = APIRouter(prefix="/api/applications", tags=["applications"])


def session_out(s) -> dict | None:
    if s is None:
        return None
    return {
        "id": s.id,
        "state": s.state,
        "message": s.message,
        "current_url": s.current_url,
        "step": s.step,
        "platform": s.platform,
        "has_screenshot": s.screenshot_png is not None,
        "log": s.log[-20:],
        "answers_revision": s.answers_revision,
        "filled_revision": s.filled_revision,
        "submit_confirmed_at": s.submit_confirmed_at.isoformat() if s.submit_confirmed_at else None,
        "result_text": s.result_text,
        "resume_version_id": s.resume_version_id,
        "last_heartbeat_at": s.last_heartbeat_at.isoformat() if s.last_heartbeat_at else None,
        "created_at": s.created_at.isoformat(),
    }


def field_out(f) -> dict:
    return {
        "id": f.id,
        "label": f.label,
        "input_type": f.input_type,
        "required": f.required,
        "options": f.options,
        "field_key": f.field_key,
        "sensitive": f.sensitive,
        "value": f.value,
        "value_source": f.value_source,
        "status": f.status,
        "note": f.note,
        "fill_result": f.fill_result,
        "validation_message": f.validation_message,
        "step": f.step,
    }


def app_out(db: Session, a: Application, job: Job | None, full: bool = False) -> dict:
    rv = db.get(ResumeVersion, a.resume_version_id) if a.resume_version_id else None
    out = {
        "id": a.id,
        "status": a.status,
        "status_label": svc.STATUS_LABELS[a.status],
        "job_id": a.job_id,
        "company": job.company if job else "",
        "role_title": job.role_title if job else "",
        "job_code": job.job_code if job else "",
        "location": job.location if job else "",
        "job_url": job.job_url if job else "",
        "application_url": job.application_url if job else "",
        "deadline": job.deadline.isoformat() if job and job.deadline else None,
        "days_remaining": days_remaining(job.deadline) if job else None,
        "discovered_at": job.created_at.isoformat() if job else None,
        "date_applied": a.date_applied.isoformat() if a.date_applied else None,
        "follow_up_date": a.follow_up_date.isoformat() if a.follow_up_date else None,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        "submission_verified": a.submission_verified,
        "notes": a.notes,
        "resume_version": {"id": rv.id, "label": rv.label, "version_number": rv.version_number} if rv else None,
        "updated_at": a.updated_at.isoformat(),
    }
    if full:
        out["events"] = [
            {"id": e.id, "kind": e.kind, "message": e.message, "data": e.data, "created_at": e.created_at.isoformat()}
            for e in reversed(a.events)
        ]
        out["answers"] = [
            {"question": x.question, "answer": x.answer, "source": x.source, "confirmed": x.confirmed}
            for x in a.answers
            if x.confirmed
        ]
        out["fields"] = [field_out(f) for f in a.fields]
        out["session"] = session_out(svc.latest_session(db, a.id))
        out["confirmation_text"] = a.confirmation_text
    return out


@router.get("")
def list_applications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Application).where(Application.user_id == user.id).order_by(Application.updated_at.desc())
    ).all()
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.user_id == user.id))}
    return [app_out(db, a, jobs.get(a.job_id)) for a in rows if not (jobs.get(a.job_id) and jobs[a.job_id].archived)]


@router.get("/{app_id}")
def get_application(app_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = svc.get_application(db, user.id, app_id)
    return app_out(db, a, db.get(Job, a.job_id), full=True)


class AppPatch(BaseModel):
    status: str | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    follow_up_date: date | None = None
    date_applied: date | None = None
    resume_version_id: int | None = None
    clear_follow_up: bool = False


@router.patch("/{app_id}")
def update_application(app_id: int, body: AppPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = svc.get_application(db, user.id, app_id)
    if body.status:
        if body.status not in APPLICATION_STATUSES:
            raise AppError("Unknown status.", code="invalid_status")
        # a status set by hand is recorded as unverified by CareerPilot
        svc.set_status(db, a, body.status, verified=False if body.status == "submitted" else None)
    if body.notes is not None:
        a.notes = body.notes
    if body.clear_follow_up:
        a.follow_up_date = None
    elif body.follow_up_date:
        a.follow_up_date = body.follow_up_date
    if body.date_applied:
        a.date_applied = body.date_applied
    if body.resume_version_id is not None:
        rv = db.get(ResumeVersion, body.resume_version_id)
        if rv is None or rv.user_id != user.id:
            raise NotFound("Resume version")
        a.resume_version_id = rv.id
    db.commit()
    return app_out(db, a, db.get(Job, a.job_id), full=True)


class NoteIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/{app_id}/events")
def add_note(app_id: int, body: NoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = svc.get_application(db, user.id, app_id)
    db.add(ApplicationEvent(application_id=a.id, kind="note", message=body.message.strip()))
    db.commit()
    return app_out(db, a, db.get(Job, a.job_id), full=True)


class StartIn(BaseModel):
    resume_version_id: int | None = None


@router.post("/{app_id}/automation")
def start_automation(app_id: int, body: StartIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = svc.get_application(db, user.id, app_id)
    svc.start_session(db, a, body.resume_version_id)
    return app_out(db, a, db.get(Job, a.job_id), full=True)


class FieldPatch(BaseModel):
    value: str | None = Field(default=None, max_length=5000)
    confirm: bool = False
    skip: bool = False


@router.patch("/{app_id}/fields/{field_id}")
def update_field(
    app_id: int, field_id: int, body: FieldPatch, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    a = svc.get_application(db, user.id, app_id)
    svc.update_field(db, a, field_id, body.value, body.confirm, body.skip)
    return app_out(db, a, db.get(Job, a.job_id), full=True)


class ConfirmIn(BaseModel):
    typed_confirmation: str = Field(default="", max_length=300)


@router.post("/{app_id}/automation/confirm-submit")
def confirm_submit(
    app_id: int, body: ConfirmIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    a = svc.get_application(db, user.id, app_id)
    svc.confirm_submit(db, a, body.typed_confirmation, client_ip(request))
    return app_out(db, a, db.get(Job, a.job_id), full=True)


@router.post("/{app_id}/automation/cancel")
def cancel(app_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = svc.get_application(db, user.id, app_id)
    svc.cancel_session(db, a)
    return app_out(db, a, db.get(Job, a.job_id), full=True)


@router.get("/{app_id}/automation/screenshot.png")
def screenshot(app_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = svc.get_application(db, user.id, app_id)
    s = svc.latest_session(db, a.id)
    if s is None or s.screenshot_png is None:
        raise NotFound("Screenshot")
    return Response(s.screenshot_png, media_type="image/png")
