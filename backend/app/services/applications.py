"""Application tracking and assisted applications.

The browser runner (app.automation.runner) fills forms on the user's machine. This module owns the rules:
which values may be filled, what needs the user, and - most importantly - that a form is submitted only after
the user explicitly confirms it for that specific application. The runner cannot move a session to
"submitted" unless the confirmation was recorded here first.
"""

import base64
import logging
import re
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.automation.fields import FieldInput, map_field, profile_context
from app.core.errors import AppError, Conflict, NotFound
from app.llm.provider import LLMError, get_provider, wrap_untrusted
from app.models import (
    APPLICATION_STATUSES,
    AUTOMATION_TERMINAL,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    ApplicationField,
    AutomationSession,
    Education,
    Experience,
    Job,
    ProfileAnswer,
    Project,
    Reminder,
    ResumeVersion,
    UserProfile,
    UserSettings,
)

log = logging.getLogger(__name__)
STATUS_LABELS = {
    "saved": "Saved",
    "analyzing": "Analyzing",
    "resume_ready": "Resume Ready",
    "ready_to_apply": "Ready to Apply",
    "in_progress": "Application In Progress",
    "submitted": "Submitted",
    "assessment": "Assessment",
    "interview": "Interview",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
    "offer": "Offer",
}


def get_application(db: Session, user_id: int, app_id: int) -> Application:
    a = db.get(Application, app_id)
    if a is None or a.user_id != user_id:
        raise NotFound("Application")
    return a


def set_status(db: Session, a: Application, status: str, note: str = "", *, verified: bool | None = None) -> None:
    if status not in APPLICATION_STATUSES:
        raise AppError("Unknown status.", code="invalid_status")
    if status == a.status:
        return
    prev = a.status
    a.status = status
    if status in ("submitted", "assessment", "interview", "rejected", "withdrawn", "offer"):
        # the application has moved past applying: an open browser session must not continue (or ever submit)
        for s in db.scalars(
            select(AutomationSession).where(
                AutomationSession.application_id == a.id, AutomationSession.state.notin_(AUTOMATION_TERMINAL)
            )
        ):
            if s.state in ("submitting",):
                continue  # the runner reports the outcome of a submission already in progress
            s.state, s.phase = "cancelled", "CANCELLED"
            s.message = f"Closed because the application was marked {STATUS_LABELS[status]}."
            _log(s, s.message, s.phase)
    if status == "submitted":
        now = datetime.now(UTC)
        a.submitted_at = a.submitted_at or now
        a.date_applied = a.date_applied or now.date()
        if verified is not None:
            a.submission_verified = verified
        settings = db.get(UserSettings, a.user_id)
        days = settings.follow_up_after_days if settings else 7
        if a.follow_up_date is None:
            a.follow_up_date = date.today() + timedelta(days=days)
        job = db.get(Job, a.job_id)
        db.execute(
            delete(Reminder).where(Reminder.job_id == a.job_id, Reminder.kind == "deadline", Reminder.done_at.is_(None))
        )
        db.add(
            Reminder(
                user_id=a.user_id,
                application_id=a.id,
                kind="follow_up",
                due_date=a.follow_up_date,
                message=f"Follow up on your {job.role_title if job else ''} application at "
                f"{job.company if job else ''}.".replace("  ", " "),
            )
        )
        if job is not None and not job.interview_prep:
            # after submitting, an interview workspace is prepared in the background
            from app.tasks.queue import enqueue

            enqueue(db, a.user_id, "interview_prep", {"job_id": job.id}, ["Preparing questions"])
    db.add(
        ApplicationEvent(
            application_id=a.id,
            kind="status",
            message=note or f"{STATUS_LABELS.get(prev, prev)} → {STATUS_LABELS[status]}",
            data={"from": prev, "to": status},
        )
    )


# --- automation sessions --------------------------------------------------------------------------


def start_session(db: Session, a: Application, resume_version_id: int | None) -> AutomationSession:
    job = db.get(Job, a.job_id)
    url = (job.application_url or job.job_url) if job else ""
    if not url:
        raise AppError("This job has no application link. Add one on the job page.", code="no_application_url")
    rv_id = resume_version_id or a.resume_version_id
    if rv_id:
        rv = db.get(ResumeVersion, rv_id)
        if rv is None or rv.user_id != a.user_id or rv.pdf is None:
            raise AppError("Choose a resume version that has a PDF.", code="invalid_resume")
        if rv.export_blocked:
            raise AppError(
                "That resume version has failing checks. Fix them on the resume page first.",
                code="export_blocked",
                status_code=409,
            )
    else:
        raise AppError("Generate a resume for this job first.", code="no_resume", actions=["generate_resume"])
    active = db.scalar(
        select(AutomationSession).where(
            AutomationSession.application_id == a.id, AutomationSession.state.notin_(AUTOMATION_TERMINAL)
        )
    )
    if active:
        raise Conflict("An application session is already open for this job.", code="session_active")
    s = AutomationSession(
        user_id=a.user_id,
        application_id=a.id,
        resume_version_id=rv_id,
        start_url=url,
        platform=job.source_platform if job else "",
        state="queued",
        phase="QUEUED",
        message="Queued for the CareerPilot Runner.",
    )
    db.add(s)
    a.resume_version_id = rv_id
    db.execute(delete(ApplicationField).where(ApplicationField.application_id == a.id))
    if a.status in ("saved", "resume_ready", "ready_to_apply"):
        set_status(db, a, "in_progress", "Application started")
    db.add(
        ApplicationEvent(
            application_id=a.id,
            kind="automation",
            message="Assisted application requested",
            data={"resume_version_id": rv_id},
        )
    )
    db.commit()
    return s


def latest_session(db: Session, app_id: int) -> AutomationSession | None:
    return db.scalar(
        select(AutomationSession)
        .where(AutomationSession.application_id == app_id)
        .order_by(AutomationSession.id.desc())
        .limit(1)
    )


RECLAIM_AFTER = timedelta(seconds=30)  # a session whose runner went silent this long can be picked up again


def claim_session(db: Session, user_id: int, token_id: int) -> AutomationSession | None:
    stale = datetime.now(UTC) - RECLAIM_AFTER
    s = db.scalar(
        select(AutomationSession)
        .where(AutomationSession.user_id == user_id, AutomationSession.state == "queued")
        .order_by(AutomationSession.id)
        .limit(1)
    )
    if s is None:
        # a session whose runner vanished (no heartbeat) can be picked up again
        s = db.scalar(
            select(AutomationSession)
            .where(
                AutomationSession.user_id == user_id,
                AutomationSession.state.notin_(AUTOMATION_TERMINAL),
                AutomationSession.state != "queued",
                AutomationSession.last_heartbeat_at < stale,
            )
            .order_by(AutomationSession.id)
            .limit(1)
        )
        if s is None:
            return None
    resumed = s.state != "queued"
    if s.state == "submitting" or s.submit_clicked_at is not None:
        # The runner stopped after clicking submit: never click again; the outcome must be checked by the user.
        s.state, s.phase = "submitted_unverified", "SUBMISSION_UNCERTAIN"
        s.message = (
            "The runner stopped while submitting, so CareerPilot cannot tell whether the employer received the "
            "application. Check the employer's portal or your email; nothing will be submitted again."
        )
        _log(s, s.message, s.phase)
        db.commit()
        return None
    if s.state == "submit_confirmed":
        # The confirmed form was lost with the old browser; the refilled form must be confirmed again.
        s.submit_confirmed_at = None
        _log(s, "Your submit confirmation was cleared because the browser restarted; review and confirm again")
    s.runner_token_id = token_id
    s.state = "running"
    s.phase = "RESUMING" if resumed else "OPENING_PAGE"
    s.message = "Runner reconnected; resuming the application..." if resumed else "Opening the application page..."
    s.last_heartbeat_at = datetime.now(UTC)
    s.filled_revision = -1
    _log(s, "Runner reconnected and is resuming" if resumed else "Runner connected and picked up the task", s.phase)
    db.commit()
    return s


def _log(s: AutomationSession, message: str, phase: str = "") -> None:
    entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "message": message[:300]}
    if phase:
        entry["phase"] = phase
    s.log = (list(s.log or []) + [entry])[-120:]


PHASES = (
    "CREATED",
    "QUEUED",
    "RUNNER_OFFLINE",
    "RUNNER_CONNECTED",
    "OPENING_PAGE",
    "INSPECTING_PAGE",
    "MAPPING_FORM",
    "FILLING_FORM",
    "UPLOADING_RESUME",
    "WAITING_FOR_USER",
    "RESUMING",
    "VALIDATING_FORM",
    "READY_TO_SUBMIT",
    "SUBMITTING",
    "VERIFYING_SUBMISSION",
    "SUBMITTED",
    "SUBMISSION_UNCERTAIN",
    "BLOCKED",
    "FAILED",
    "CANCELLED",
)
_STATE_PHASE = {
    "action_required": "WAITING_FOR_USER",
    "awaiting_answers": "WAITING_FOR_USER",
    "awaiting_review": "READY_TO_SUBMIT",
    "submit_confirmed": "SUBMITTING",
    "submitted": "SUBMITTED",
    "submitted_unverified": "SUBMISSION_UNCERTAIN",
    "unsupported": "BLOCKED",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
}


def display_phase(s: AutomationSession, runner_online: bool) -> str:
    if s.state == "queued":
        return "QUEUED" if runner_online else "RUNNER_OFFLINE"
    if s.state not in AUTOMATION_TERMINAL and not runner_online:
        return "RUNNER_OFFLINE"  # whatever it was doing, nothing happens until the runner is back
    if s.state in _STATE_PHASE:
        return _STATE_PHASE[s.state]
    return s.phase if s.phase in PHASES else "INSPECTING_PAGE"


def record_event(db: Session, s: AutomationSession, phase: str, message: str) -> None:
    if phase in PHASES:
        s.phase = phase
    s.last_heartbeat_at = datetime.now(UTC)
    _log(s, message, phase)


RUNNER_ONLINE_SECONDS = 15


def runner_status(db: Session, user_id: int) -> dict:
    """Whether a CareerPilot Runner is connected for this user right now, and what it is doing."""
    from app.models import ApiToken

    now = datetime.now(UTC)
    tokens = db.scalars(select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))).all()
    runners = []
    for t in tokens:
        if t.runner_seen_at is None:
            continue
        age = (now - t.runner_seen_at).total_seconds()
        runners.append(
            {
                "name": t.name,
                "host": t.runner_host,
                "version": t.runner_version,
                "online": age <= RUNNER_ONLINE_SECONDS,
                "state": t.runner_state if age <= RUNNER_ONLINE_SECONDS else "offline",
                "detail": t.runner_detail,
                "last_seen_at": t.runner_seen_at.isoformat(),
                "last_seen_seconds": int(age),
            }
        )
    runners.sort(key=lambda r: r["last_seen_seconds"])
    online = [r for r in runners if r["online"]]
    return {
        "connected": bool(online),
        "state": online[0]["state"] if online else "offline",
        "detail": online[0]["detail"] if online else "",
        "installed": bool(tokens),
        "ever_connected": bool(runners),
        "runners": runners,
    }


def continue_session(db: Session, a: Application) -> AutomationSession:
    s = latest_session(db, a.id)
    if s is None or s.state in AUTOMATION_TERMINAL:
        raise AppError("There is no application in progress to continue.", code="not_running")
    s.resume_nonce += 1
    _log(s, "You asked the agent to continue", "RESUMING")
    db.commit()
    return s


def get_runner_session(db: Session, user_id: int, token_id: int, session_id: int) -> AutomationSession:
    s = db.get(AutomationSession, session_id)
    if s is None or s.user_id != user_id or s.runner_token_id != token_id:
        raise NotFound("Session")
    return s


def _draft_fn(db: Session, a: Application):
    settings = db.get(UserSettings, a.user_id)
    provider = get_provider(settings)
    if provider is None:
        return None
    job = db.get(Job, a.job_id)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == a.user_id))
    projects = db.scalars(select(Project).where(Project.user_id == a.user_id, Project.hidden.is_(False))).all()
    facts = {
        "summary": profile.summary if profile else "",
        "projects": [f"{p.title}: {p.description}" for p in projects[:8]],
        "skills": [],
    }

    def draft(question: str) -> str:
        system = (
            "Draft a concise, sincere answer (80-150 words) to a job application question, in first person, "
            "using only the candidate facts given. Do not invent experience, numbers, or feelings about "
            'specific people. Return JSON {"answer": "..."}.'
        )
        prompt = (
            f"Company: {job.company if job else ''}\nRole: {job.role_title if job else ''}\n"
            f"Candidate facts: {facts}\n\n"
            + wrap_untrusted("QUESTION", question, 600)
            + "\n\n"
            + wrap_untrusted("JOB DESCRIPTION", (job.description_text if job else "")[:6000], 6000)
        )
        try:
            data = provider.complete_json(system, prompt, max_tokens=600, temperature=0.4)
        except LLMError:
            return ""
        ans = data.get("answer")
        return ans.strip()[:2000] if isinstance(ans, str) else ""

    return draft


def ingest_fields(db: Session, s: AutomationSession, fields: list[dict]) -> None:
    """Stores fields the runner found on the current form step and maps each to a proposed value."""
    a = db.get(Application, s.application_id)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == a.user_id))
    educations = db.scalars(
        select(Education).where(Education.user_id == a.user_id).order_by(Education.sort_order)
    ).all()
    experiences = db.scalars(
        select(Experience).where(Experience.user_id == a.user_id).order_by(Experience.sort_order)
    ).all()
    saved = {
        pa.question_key: (pa.answer, pa.sensitive)
        for pa in db.scalars(select(ProfileAnswer).where(ProfileAnswer.user_id == a.user_id))
    }
    rv = db.get(ResumeVersion, s.resume_version_id) if s.resume_version_id else None
    ctx = {
        "profile": profile_context(profile, educations, experiences),
        "saved_answers": saved,
        "resume_version_id": s.resume_version_id,
        "resume_label": rv.label if rv else "",
        "draft_fn": _draft_fn(db, a),
    }
    existing = {f.field_ref: f for f in a.fields}
    # After a page reload or a runner restart the same fields come back with new references.
    by_shape = {(f.label, f.input_type, f.step): f for f in a.fields}
    pos = max([f.position for f in a.fields], default=-1) + 1
    for raw in fields[:200]:
        ref = str(raw.get("field_ref", ""))[:300]
        if not ref:
            continue
        fi = FieldInput(
            field_ref=ref,
            label=str(raw.get("label", ""))[:1000] or "(unlabelled field)",
            input_type=str(raw.get("input_type", "text"))[:30],
            required=bool(raw.get("required")),
            options=[str(o)[:200] for o in (raw.get("options") or [])][:200],
            step=int(raw.get("step", 0)),
        )
        same = by_shape.get((fi.label, fi.input_type, fi.step))
        if ref not in existing and same is not None:
            existing.pop(same.field_ref, None)
            same.field_ref = ref
            existing[ref] = same
        if ref in existing:
            f = existing[ref]
            f.required, f.options, f.step = fi.required, fi.options or [], fi.step
            if f.value_source == "user" or f.status == "confirmed":
                continue
        else:
            f = ApplicationField(application_id=a.id, field_ref=ref, position=pos)
            pos += 1
            db.add(f)
            a.fields.append(f)
        m = map_field(fi, ctx)
        f.label, f.input_type, f.required, f.options, f.step = (
            fi.label,
            fi.input_type,
            fi.required,
            fi.options or [],
            fi.step,
        )
        f.field_key, f.sensitive, f.value, f.value_source, f.status, f.note = (
            m.field_key,
            m.sensitive,
            m.value,
            m.source,
            m.status,
            m.note,
        )
        if m.source == "draft" and m.value:
            db.add(ApplicationAnswer(application_id=a.id, question=fi.label, answer=m.value, source="draft"))
    s.answers_revision += 1
    s.state = "filling"
    s.phase = "FILLING_FORM"
    s.message = "Filling the fields CareerPilot can answer..."
    _update_progress(s, a)
    auto = sum(1 for f in a.fields if f.step == s.step and f.status in ("filled", "confirmed"))
    _log(
        s,
        f"{len(fields)} fields identified on page {s.step + 1}; {auto} can be filled from your profile",
        "MAPPING_FORM",
    )
    db.commit()


def _update_progress(s: AutomationSession, a: Application) -> None:
    relevant = [
        f for f in a.fields if f.required or f.value or f.status == "needs_confirmation" or f.field_key == "consent"
    ]
    s.fields_total = len(relevant)
    s.fields_done = sum(
        1 for f in relevant if f.status in ("filled", "confirmed", "skipped") and (f.value or f.status == "skipped")
    )


def fill_plan(db: Session, s: AutomationSession) -> list[dict]:
    """What the runner may put into the form: only filled/confirmed values, never pending or sensitive ones."""
    a = db.get(Application, s.application_id)
    out = []
    for f in a.fields:
        if f.status not in ("filled", "confirmed") or not f.value:
            continue
        if f.sensitive and f.status != "confirmed":
            continue
        action = "upload" if f.input_type == "file" else ("check" if f.input_type == "checkbox" else "fill")
        out.append(
            {"field_ref": f.field_ref, "value": f.value, "action": action, "input_type": f.input_type, "step": f.step}
        )
    return out


def unresolved(a: Application) -> list[ApplicationField]:
    return [
        f
        for f in a.fields
        if f.status in ("needs_input", "needs_confirmation")
        and (f.required or f.status == "needs_confirmation" or f.field_key == "consent")
    ]


def apply_report(db: Session, s: AutomationSession, report: dict) -> None:
    """Applies a runner status report. Enforces that submission follows explicit user confirmation."""
    a = db.get(Application, s.application_id)
    state = str(report.get("state", s.state))
    s.last_heartbeat_at = datetime.now(UTC)
    if report.get("current_url"):
        s.current_url = str(report["current_url"])[:1000]
    if report.get("step") is not None:
        s.step = int(report["step"])
    if report.get("message"):
        s.message = str(report["message"])[:1000]
        _log(s, s.message)
    shot = report.get("screenshot_b64")
    if isinstance(shot, str) and len(shot) < 4_000_000:
        try:
            png = base64.b64decode(shot, validate=True)
            if png.startswith(b"\x89PNG"):
                s.screenshot_png = png
        except ValueError:
            pass
    if isinstance(report.get("filled_revision"), int):
        s.filled_revision = report["filled_revision"]
    if report.get("phase") in PHASES:
        s.phase = report["phase"]
    if report.get("platform"):
        s.platform = str(report["platform"])[:40]
    if report.get("resume_uploaded") is not None:
        s.resume_uploaded = bool(report["resume_uploaded"])
    by_ref = {f.field_ref: f for f in a.fields}
    for r in report.get("fill_results") or []:
        f = by_ref.get(str(r.get("field_ref")))
        if f is None:
            continue
        f.fill_result = "ok" if r.get("ok") else "failed"
        f.validation_message = str(r.get("validation_message") or "")[:500]
        if not r.get("ok") and f.status == "filled":
            f.note = "The page did not accept this value automatically - fill it in the browser."

    confirmed = s.submit_confirmed_at is not None and s.state in ("submit_confirmed", "submitting")
    if state in ("submitted", "submitted_unverified", "submitting") and not confirmed:
        _log(s, "Rejected a submission report that was not preceded by your confirmation")
        db.commit()
        raise AppError("Submission was not confirmed by the user.", code="submit_not_confirmed", status_code=409)
    allowed = {
        "running",
        "action_required",
        "awaiting_answers",
        "filling",
        "awaiting_review",
        "submitting",
        "submitted",
        "submitted_unverified",
        "unsupported",
        "failed",
    }
    if state not in allowed:
        raise AppError("Unknown session state.", code="invalid_state")
    if state == "awaiting_review" and unresolved(a):
        state = "awaiting_answers"
    if s.state == "submit_confirmed" and state in ("awaiting_review", "awaiting_answers", "filling", "running"):
        # the runner is still settling; keep the user's confirmation pending
        state = "submit_confirmed"
    if state == "submitting" and s.submit_clicked_at is None:
        s.submit_clicked_at = datetime.now(UTC)
    s.state = state
    if state in _STATE_PHASE and state != "submit_confirmed":
        s.phase = _STATE_PHASE[state]
    _update_progress(s, a)

    if state == "submitted":
        s.result_text = str(report.get("result_text") or "")[:2000]
        a.confirmation_text = s.result_text
        a.confirmation_id = str(report.get("confirmation_id") or "")[:120]
        note = "Application submitted - confirmation detected on the page"
        if a.confirmation_id:
            note += f" (application ID {a.confirmation_id})"
        set_status(db, a, "submitted", note, verified=True)
        db.add(
            ApplicationEvent(
                application_id=a.id,
                kind="submission",
                message="Submitted through the apply agent",
                data={"url": s.current_url, "resume_version_id": s.resume_version_id},
            )
        )
        _record_answers(db, a)
    elif state == "submitted_unverified":
        s.result_text = str(report.get("result_text") or "")[:2000]
        db.add(
            ApplicationEvent(
                application_id=a.id,
                kind="submission",
                message="Submit was clicked, but the submission status could not be verified. "
                "Check the application portal.",
                data={"url": s.current_url},
            )
        )
    elif state in ("unsupported", "failed"):
        db.add(ApplicationEvent(application_id=a.id, kind="automation", message=s.message or state))
    db.commit()


def _record_answers(db: Session, a: Application) -> None:
    for f in a.fields:
        if f.value and f.status in ("filled", "confirmed") and f.input_type != "file":
            db.add(
                ApplicationAnswer(
                    application_id=a.id,
                    field_id=f.id,
                    question=f.label,
                    answer=f.value if not f.sensitive else f.value,
                    source=f.value_source,
                    confirmed=True,
                )
            )


def update_field(
    db: Session, a: Application, field_id: int, value: str | None, confirm: bool, skip: bool, remember: bool = False
) -> None:
    f = next((x for x in a.fields if x.id == field_id), None)
    if f is None:
        raise NotFound("Field")
    if skip:
        if f.required:
            raise AppError("This field is required by the employer and can't be skipped.", code="required_field")
        f.status, f.value, f.value_source = "skipped", "", "user"
    else:
        if value is not None:
            value = value.strip()[:5000]
            if f.options and f.input_type in ("select", "radio", "combobox") and value and value not in f.options:
                raise AppError("Choose one of the options the form offers.", code="invalid_option")
            if value != f.value:
                f.value, f.value_source = value, "user"
        if confirm:
            if not f.value and f.field_key != "consent":
                raise AppError("Enter an answer before confirming.", code="empty_answer")
            if f.field_key == "consent":
                f.value = "true"
            f.status = "confirmed"
        elif f.value:
            f.status = (
                "filled"
                if not (
                    f.sensitive
                    or f.field_key in ("work_authorization", "sponsorship", "consent")
                    or f.value_source == "draft"
                )
                else "needs_confirmation"
            )
        else:
            f.status = "needs_input"
    if remember and f.value and f.status != "skipped":
        _remember_answer(db, a.user_id, f)
    s = latest_session(db, a.id)
    if s and s.state not in AUTOMATION_TERMINAL:
        _update_progress(s, a)
        s.answers_revision += 1
        if s.state in ("awaiting_answers", "awaiting_review"):
            s.state = "filling"
            s.message = "Applying your answers..."
    db.commit()


def _remember_answer(db: Session, user_id: int, f: ApplicationField) -> None:
    """Saves an answer for future applications - only when the user explicitly asked to remember it."""
    key = (f.field_key or re.sub(r"[^a-z0-9]+", "_", f.label.lower()).strip("_"))[:80]
    if not key or f.input_type == "file":
        return
    pa = db.scalar(select(ProfileAnswer).where(ProfileAnswer.user_id == user_id, ProfileAnswer.question_key == key))
    if pa is None:
        pa = ProfileAnswer(user_id=user_id, question_key=key)
        db.add(pa)
    pa.label, pa.answer, pa.sensitive = f.label[:300], f.value, bool(f.sensitive)


PROVENANCE = {
    "user": "USER_CONFIRMED",
    "saved_answer": "USER_CONFIRMED",
    "profile": "CAREER_PROFILE",
    "resume": "CAREER_PROFILE",
    "job": "JOB_SPECIFIC",
    "draft": "AI_SUGGESTED",
}


def confirm_submit(db: Session, a: Application, typed: str, ip: str = "") -> AutomationSession:
    s = latest_session(db, a.id)
    if s is None or s.state != "awaiting_review":
        raise AppError(
            "The application is not ready for submission yet.",
            code="not_ready",
            hint="Wait until every field is filled and the agent reports it is ready for review.",
        )
    pending = unresolved(a)
    if pending:
        raise AppError(f"{len(pending)} field(s) still need your answer or confirmation.", code="fields_pending")
    if s.filled_revision < s.answers_revision:
        raise AppError("Your latest answers are still being applied. Try again in a moment.", code="not_ready")
    settings = db.get(UserSettings, a.user_id)
    job = db.get(Job, a.job_id)
    must_type = (settings is None or settings.submit_confirmation_mode == "type_company") and job and job.company
    if must_type and _loose(typed) != _loose(job.company):
        raise AppError(
            f"Type the company name ({job.company}) exactly to confirm submission.", code="confirmation_mismatch"
        )
    s.state = "submit_confirmed"
    s.submit_confirmed_at = datetime.now(UTC)
    s.message = "You confirmed submission. The agent will submit the form now."
    _log(s, "User confirmed submission")
    db.add(
        ApplicationEvent(
            application_id=a.id, kind="submission", message="You confirmed final submission", data={"session_id": s.id}
        )
    )
    db.commit()
    return s


def cancel_session(db: Session, a: Application) -> None:
    s = latest_session(db, a.id)
    if s and s.state not in AUTOMATION_TERMINAL:
        s.state = "cancelled"
        s.message = "Cancelled. Nothing was submitted."
        _log(s, "Cancelled by the user")
        db.commit()


def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


SAFE_KEYS = {
    "first_name",
    "last_name",
    "preferred_name",
    "full_name",
    "email",
    "phone",
    "linkedin",
    "github",
    "portfolio",
    "current_company",
    "current_title",
    "school",
    "degree",
    "discipline",
    "graduation_year",
    "gpa",
    "location",
    "country",
    "resume",
    "cover_letter",
}
PROFILE_REQUIRED_KEYS = {"years_experience", "work_authorization", "sponsorship", "notice_period", "start_date"}
JOB_DECISION_KEYS = {"salary", "relocate", "how_heard", "referral", "why_company", "additional_info"}


def question_category(f: ApplicationField) -> str:
    """How much judgment a question needs: safe auto-fill, verified profile fact, job-specific decision, sensitive."""
    if f.sensitive:
        return "SENSITIVE"
    if f.field_key in SAFE_KEYS:
        return "SAFE_AUTOFILL"
    if f.field_key in PROFILE_REQUIRED_KEYS:
        return "VERIFIED_PROFILE_REQUIRED"
    return "JOB_SPECIFIC_DECISION"
