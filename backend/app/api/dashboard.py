import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.security import verify_password
from app.matching.taxonomy import display_name
from app.models import (
    ACTIVE_STATUSES,
    AUTOMATION_TERMINAL,
    Application,
    AutomationSession,
    BackgroundTask,
    Job,
    JobMatch,
    JobRequirement,
    Reminder,
    ResumeVersion,
    User,
    UserProfile,
)
from app.services.jobs import days_remaining

router = APIRouter(prefix="/api", tags=["dashboard"])
PIPELINE = [
    ("saved", ["saved", "analyzing"]),
    ("ready", ["resume_ready", "ready_to_apply", "in_progress"]),
    ("applied", ["submitted"]),
    ("assessment", ["assessment"]),
    ("interview", ["interview"]),
    ("offer", ["offer"]),
]


@router.get("/tasks/{task_id}")
def get_task(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(BackgroundTask, task_id)
    if t is None or t.user_id != user.id:
        raise NotFound("Task")
    return {
        "id": t.id,
        "kind": t.kind,
        "status": t.status,
        "steps": t.steps,
        "result": t.result,
        "error": t.error,
        "created_at": t.created_at.isoformat(),
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
    }


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    today = date.today()
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    first = profile.first_name if profile and profile.first_name else (user.display_name or "").split(" ")[0]
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.user_id == user.id, Job.archived.is_(False)))}
    apps = [a for a in db.scalars(select(Application).where(Application.user_id == user.id)) if a.job_id in jobs]
    versions = db.scalars(
        select(ResumeVersion).where(ResumeVersion.user_id == user.id, ResumeVersion.is_base.is_(False))
    ).all()
    sessions = db.scalars(
        select(AutomationSession).where(
            AutomationSession.user_id == user.id, AutomationSession.state.notin_(AUTOMATION_TERMINAL)
        )
    ).all()

    def card(a: Application) -> dict:
        j = jobs[a.job_id]
        return {
            "application_id": a.id,
            "job_id": j.id,
            "company": j.company,
            "role_title": j.role_title,
            "location": j.location,
            "status": a.status,
            "deadline": j.deadline.isoformat() if j.deadline else None,
            "days_remaining": days_remaining(j.deadline),
            "analysis_state": j.analysis_state,
        }

    active = [a for a in apps if a.status in ACTIVE_STATUSES]
    upcoming = sorted(
        [
            a
            for a in active
            if jobs[a.job_id].deadline
            and 0 <= days_remaining(jobs[a.job_id].deadline) <= 14
            and a.status not in ("submitted", "assessment", "interview")
        ],
        key=lambda a: jobs[a.job_id].deadline,
    )
    overdue = [
        a
        for a in active
        if jobs[a.job_id].deadline
        and days_remaining(jobs[a.job_id].deadline) < 0
        and a.status in ("saved", "resume_ready", "ready_to_apply", "in_progress")
    ]
    ready = [a for a in apps if a.status in ("resume_ready", "ready_to_apply")]
    needs_review = [
        a
        for a in apps
        if any(
            s.application_id == a.id and s.state in ("awaiting_answers", "awaiting_review", "action_required")
            for s in sessions
        )
    ]
    failed_analysis = [a for a in apps if jobs[a.job_id].analysis_state == "failed"]
    reminders = db.scalars(
        select(Reminder)
        .where(Reminder.user_id == user.id, Reminder.done_at.is_(None), Reminder.due_date <= today + timedelta(days=7))
        .order_by(Reminder.due_date)
    ).all()
    followups_due = [r for r in reminders if r.kind == "follow_up" and r.due_date <= today]

    actions = []
    if needs_review:
        actions.append(
            {
                "kind": "review",
                "count": len(needs_review),
                "text": f"{len(needs_review)} application{'s' if len(needs_review) > 1 else ''} need"
                f"{'' if len(needs_review) > 1 else 's'} your review",
                "items": [card(a) for a in needs_review[:5]],
            }
        )
    if ready:
        actions.append(
            {
                "kind": "ready",
                "count": len(ready),
                "text": f"{len(ready)} application{'s' if len(ready) > 1 else ''} ready to apply",
                "items": [card(a) for a in ready[:5]],
            }
        )
    soon = [a for a in upcoming if days_remaining(jobs[a.job_id].deadline) <= 5]
    if soon:
        actions.append(
            {
                "kind": "deadline",
                "count": len(soon),
                "text": f"{len(soon)} deadline{'s' if len(soon) > 1 else ''} in the next 5 days",
                "items": [card(a) for a in soon[:5]],
            }
        )
    if overdue:
        actions.append(
            {
                "kind": "overdue",
                "count": len(overdue),
                "text": f"{len(overdue)} deadline{'s' if len(overdue) > 1 else ''} passed before you applied",
                "items": [card(a) for a in overdue[:5]],
            }
        )
    if followups_due:
        actions.append(
            {
                "kind": "follow_up",
                "count": len(followups_due),
                "text": f"{len(followups_due)} follow-up{'s' if len(followups_due) > 1 else ''} due",
                "items": [
                    {"reminder_id": r.id, "text": r.message, "application_id": r.application_id}
                    for r in followups_due[:5]
                ],
            }
        )
    if failed_analysis:
        actions.append(
            {
                "kind": "failed",
                "count": len(failed_analysis),
                "text": f"{len(failed_analysis)} job{'s' if len(failed_analysis) > 1 else ''} could not be analysed",
                "items": [card(a) for a in failed_analysis[:5]],
            }
        )

    pipeline = [{"stage": stage, "count": sum(1 for a in apps if a.status in statuses)} for stage, statuses in PIPELINE]
    recent = sorted(apps, key=lambda a: jobs[a.job_id].created_at, reverse=True)[:8]
    counts = {}
    if recent:
        rows = db.execute(
            select(JobMatch.job_id, JobMatch.status, JobRequirement.importance)
            .join(JobRequirement, JobRequirement.id == JobMatch.requirement_id)
            .where(JobMatch.job_id.in_([a.job_id for a in recent]))
        ).all()
        for job_id, status, importance in rows:
            c = counts.setdefault(job_id, {"required_total": 0, "required_strong": 0, "missing": 0})
            if importance == "required":
                c["required_total"] += 1
                c["required_strong"] += status == "strong"
                c["missing"] += status == "missing"
    return {
        "greeting_name": first,
        "stats": {
            "active_applications": len(active),
            "resume_versions": len(versions),
            "upcoming_deadlines": len(upcoming),
            "interviews": sum(1 for a in apps if a.status == "interview"),
            "needs_attention": len(needs_review) + len(overdue) + len(failed_analysis) + len(followups_due),
        },
        "actions": actions,
        "pipeline": pipeline,
        "recent_jobs": [dict(card(a), match=counts.get(a.job_id, {})) for a in recent],
        "upcoming_deadlines": [card(a) for a in upcoming[:6]],
        "reminders": [
            {
                "id": r.id,
                "kind": r.kind,
                "due_date": r.due_date.isoformat(),
                "message": r.message,
                "application_id": r.application_id,
                "job_id": r.job_id,
            }
            for r in reminders
        ],
        "has_jobs": bool(jobs),
    }


@router.get("/reminders")
def list_reminders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Reminder).where(Reminder.user_id == user.id, Reminder.done_at.is_(None)).order_by(Reminder.due_date)
    )
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "due_date": r.due_date.isoformat(),
            "message": r.message,
            "application_id": r.application_id,
            "job_id": r.job_id,
            "overdue": r.due_date < date.today(),
        }
        for r in rows
    ]


class ReminderIn(BaseModel):
    due_date: date
    message: str = Field(min_length=1, max_length=500)
    application_id: int | None = None


@router.post("/reminders")
def add_reminder(body: ReminderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.application_id:
        a = db.get(Application, body.application_id)
        if a is None or a.user_id != user.id:
            raise NotFound("Application")
    r = Reminder(
        user_id=user.id,
        application_id=body.application_id,
        kind="custom",
        due_date=body.due_date,
        message=body.message.strip(),
    )
    db.add(r)
    db.commit()
    return {"id": r.id}


@router.post("/reminders/{rid}/done")
def reminder_done(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Reminder, rid)
    if r is None or r.user_id != user.id:
        raise NotFound("Reminder")
    r.done_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


@router.get("/analytics")
def analytics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.user_id == user.id))}
    apps = list(db.scalars(select(Application).where(Application.user_id == user.id)))
    submitted = [
        a for a in apps if a.submitted_at or a.status in ("submitted", "assessment", "interview", "offer", "rejected")
    ]
    by_month = Counter(
        (a.date_applied or (a.submitted_at.date() if a.submitted_at else None)).strftime("%Y-%m")
        for a in submitted
        if (a.date_applied or a.submitted_at)
    )
    reached_interview = [
        a
        for a in submitted
        if a.status in ("interview", "offer") or any(e.data.get("to") == "interview" for e in a.events)
    ]
    reqs = db.execute(
        select(JobRequirement.canonical, JobRequirement.importance, JobMatch.status)
        .join(Job, Job.id == JobRequirement.job_id)
        .outerjoin(JobMatch, JobMatch.requirement_id == JobRequirement.id)
        .where(Job.user_id == user.id, JobRequirement.kind == "skill")
    ).all()
    requested = Counter(c for c, imp, _ in reqs if c and imp in ("required", "preferred"))
    missing = Counter(c for c, imp, st in reqs if c and st == "missing" and imp in ("required", "preferred"))
    companies = Counter(jobs[a.job_id].company or "Unknown" for a in apps if a.job_id in jobs)
    statuses = Counter(a.status for a in apps)
    versions = db.scalars(
        select(ResumeVersion).where(ResumeVersion.user_id == user.id, ResumeVersion.is_base.is_(False))
    ).all()
    return {
        "applications_by_month": sorted(by_month.items()),
        "applications_this_month": by_month.get(date.today().strftime("%Y-%m"), 0),
        "submitted_total": len(submitted),
        "interview_conversion": {"interviews": len(reached_interview), "submitted": len(submitted)},
        "status_counts": dict(statuses),
        "most_requested": [{"skill": display_name(k), "count": n} for k, n in requested.most_common(12)],
        "most_missing": [{"skill": display_name(k), "count": n} for k, n in missing.most_common(10)],
        "by_company": [{"company": k, "count": n} for k, n in companies.most_common(12)],
        "resume_versions": len(versions),
        "jobs_analyzed": sum(1 for j in jobs.values() if j.analysis_state == "ready"),
    }


@router.get("/data/export")
def export_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Everything CareerPilot stores for this account, as JSON (binary files and secrets excluded)."""
    from sqlalchemy import inspect

    from app import models

    out = {"exported_at": datetime.now(UTC).isoformat(), "account": {"email": user.email}}
    skip_cols = {
        "password_hash",
        "gemini_key_encrypted",
        "openrouter_key_encrypted",
        "github_token_encrypted",
        "token_hash",
        "pdf",
        "source_bytes",
        "preview_pdf",
        "screenshot_png",
        "csrf_token",
    }
    for name in (
        "UserProfile",
        "Experience",
        "Education",
        "Skill",
        "Certification",
        "Achievement",
        "ProfileAnswer",
        "Project",
        "Job",
        "Application",
        "ResumeVersion",
        "SuggestedProject",
        "Reminder",
    ):
        model = getattr(models, name)
        rows = db.scalars(select(model).where(model.user_id == user.id)).all()
        cols = [c.key for c in inspect(model).mapper.column_attrs if c.key not in skip_cols]
        out[name] = [{c: _jsonable(getattr(r, c)) for c in cols} for r in rows]
    body = json.dumps(out, indent=1, ensure_ascii=False)
    return Response(
        body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="careerpilot-export.json"'},
    )


def _jsonable(v):
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, bytes):
        return None
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


class DeleteIn(BaseModel):
    password: str = Field(max_length=256)
    confirm: str = Field(max_length=40)


@router.post("/account/delete")
def delete_account(body: DeleteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.confirm != "DELETE" or not verify_password(user.password_hash, body.password):
        raise AppError("Enter your password and type DELETE to confirm.", code="confirmation_failed")
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    return {"ok": True}
