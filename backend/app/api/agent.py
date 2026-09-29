"""Agent status and commands for the web app (session-authenticated)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.models import AUTOMATION_TERMINAL, Application, AutomationSession, Job, ResumeVersion, User
from app.services import applications as svc

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/status")
def status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Runner connection plus the application the agent is working on, for the always-visible status panel."""
    runner = svc.runner_status(db, user.id)
    s = db.scalar(
        select(AutomationSession)
        .where(AutomationSession.user_id == user.id, AutomationSession.state.notin_(AUTOMATION_TERMINAL))
        .order_by(AutomationSession.id.desc())
        .limit(1)
    )
    task = None
    if s is not None:
        a = db.get(Application, s.application_id)
        job = db.get(Job, a.job_id) if a else None
        rv = db.get(ResumeVersion, s.resume_version_id) if s.resume_version_id else None
        pending = svc.unresolved(a) if a else []
        task = {
            "application_id": s.application_id,
            "company": job.company if job else "",
            "role_title": job.role_title if job else "",
            "phase": svc.display_phase(s, runner["connected"]),
            "message": s.message,
            "resume_version": rv.version_number if rv else None,
            "fields_total": s.fields_total,
            "fields_done": s.fields_done,
            "waiting_for": [f.label for f in pending][:3],
        }
    return {"runner": runner, "task": task}


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    context: dict = Field(default_factory=dict)


@router.post("/command")
def command(body: CommandIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Natural-language command from the command palette: the agent picks a tool, runs it and reports."""
    from app.agent import command as agent_command
    from app.llm.provider import get_provider
    from app.models import UserSettings

    provider = get_provider(db.get(UserSettings, user.id))
    return agent_command.run(db, user.id, body.text, body.context, provider)
