"""The agent's tool registry.

Every action the command agent can take is a named tool with a declared permission:

    READ         reads the user's own data (job, requirements, resume, evidence, application)
    WRITE        changes CareerPilot data (resume drafts, notes, tracker, scans)
    EXTERNAL     acts outside CareerPilot through the user's runner (browser navigation, form filling)
    HIGH_IMPACT  irreversible outside effects (final application submission) - never executed by a command;
                 the user is taken to the confirmation screen instead

The agent chooses tools; it never touches application state directly. Tools receive only the signed-in
user's data (every lookup is scoped by user id).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    AUTOMATION_TERMINAL,
    Application,
    Job,
    JobMatch,
    JobRequirement,
    ResumeVersion,
    UserSettings,
)

READ, WRITE, EXTERNAL, HIGH_IMPACT = "READ", "WRITE", "EXTERNAL", "HIGH_IMPACT"


@dataclass
class Ctx:
    db: Session
    user_id: int
    job_id: int | None = None
    application_id: int | None = None
    resume_id: int | None = None
    selection: list[str] = field(default_factory=list)
    text: str = ""
    notes: list[str] = field(default_factory=list)  # e.g. "Using your most recent job"


@dataclass
class Result:
    report: str
    navigate: str = ""
    actions: list[dict] = field(default_factory=list)  # [{label, href}]
    data: dict = field(default_factory=dict)
    ok: bool = True


@dataclass(frozen=True)
class Tool:
    name: str
    permission: str
    description: str
    needs: tuple[str, ...]  # context it requires: "job", "application", "resume"
    run: Callable[[Ctx], Result]


REGISTRY: dict[str, Tool] = {}


def tool(name: str, permission: str, description: str, needs: tuple[str, ...] = ()):
    def deco(fn: Callable[[Ctx], Result]):
        REGISTRY[name] = Tool(name, permission, description, needs, fn)
        return fn

    return deco


# --- context helpers ----------------------------------------------------------------------------------


def resolve(ctx: Ctx, needs: tuple[str, ...]) -> None:
    """Fills in the job / application / resume a command refers to from the page the user is on."""
    db = ctx.db
    if ctx.resume_id and not ctx.job_id:
        v = _owned(db, ResumeVersion, ctx.resume_id, ctx.user_id)
        ctx.job_id = v.job_id
    if ctx.application_id and not ctx.job_id:
        ctx.job_id = _owned(db, Application, ctx.application_id, ctx.user_id).job_id
    if ("job" in needs or "application" in needs or "resume" in needs) and not ctx.job_id:
        job = db.scalar(
            select(Job).where(Job.user_id == ctx.user_id, Job.archived.is_(False)).order_by(Job.updated_at.desc())
        )
        if job is None:
            raise AppError("There is no job yet. Analyze a job first.", code="no_job", actions=["analyze_job"])
        ctx.job_id = job.id
        ctx.notes.append(f"Using your most recent job: {job.role_title} at {job.company}")
    if "application" in needs and not ctx.application_id:
        a = db.scalar(select(Application).where(Application.user_id == ctx.user_id, Application.job_id == ctx.job_id))
        if a is None:
            raise AppError("This job has no application record yet.", code="no_application")
        ctx.application_id = a.id
    if "resume" in needs and not ctx.resume_id:
        v = db.scalar(
            select(ResumeVersion)
            .where(
                ResumeVersion.user_id == ctx.user_id,
                ResumeVersion.job_id == ctx.job_id,
                ResumeVersion.is_base.is_(False),
            )
            .order_by(ResumeVersion.created_at.desc())
        )
        if v is None:
            raise AppError("There is no resume for this job yet.", code="no_resume", actions=["generate_resume"])
        ctx.resume_id = v.id


def _owned(db: Session, model, oid: int, user_id: int):
    obj = db.get(model, oid)
    if obj is None or obj.user_id != user_id:
        raise AppError("Not found.", code="not_found", status_code=404)
    return obj


def _job(ctx: Ctx) -> Job:
    return _owned(ctx.db, Job, ctx.job_id, ctx.user_id)


def _label(job: Job) -> str:
    return f"{job.role_title or 'the role'} at {job.company or 'the company'}"


# --- READ ---------------------------------------------------------------------------------------------


@tool("get_skill_gaps", READ, "Requirements of the job with no or partial evidence", ("job",))
def get_skill_gaps(ctx: Ctx) -> Result:
    job = _job(ctx)
    rows = ctx.db.execute(
        select(JobRequirement, JobMatch)
        .join(JobMatch, JobMatch.requirement_id == JobRequirement.id)
        .where(JobRequirement.job_id == job.id, JobMatch.status.in_(("missing", "partial")))
        .order_by(JobRequirement.importance, JobRequirement.position)
    ).all()
    missing = [r.label for r, m in rows if m.status == "missing"]
    partial = [r.label for r, m in rows if m.status == "partial"]
    if not rows:
        return Result(f"No gaps for {_label(job)}: every requirement has evidence.", f"/jobs/{job.id}")
    parts = []
    if missing:
        parts.append(f"Missing evidence: {', '.join(missing[:8])}")
    if partial:
        parts.append(f"Partial evidence: {', '.join(partial[:8])}")
    return Result(
        f"Gaps for {_label(job)}. " + ". ".join(parts) + ".",
        f"/jobs/{job.id}",
        data={"missing": missing, "partial": partial},
    )


@tool("application_blockers", READ, "What is stopping the application right now", ("job", "application"))
def application_blockers(ctx: Ctx) -> Result:
    from app.services import applications as svc

    db = ctx.db
    a = _owned(db, Application, ctx.application_id, ctx.user_id)
    job = _job(ctx)
    href = f"/applications/{a.id}"
    s = svc.latest_session(db, a.id)
    runner = svc.runner_status(db, ctx.user_id)
    reasons: list[str] = []
    if a.status == "submitted":
        return Result(
            f"Nothing: the application to {_label(job)} was submitted"
            + (" and confirmed by the employer." if a.submission_verified else "."),
            href,
        )
    if not a.resume_version_id and not db.scalar(
        select(ResumeVersion.id).where(ResumeVersion.job_id == job.id, ResumeVersion.user_id == ctx.user_id)
    ):
        reasons.append("there is no resume for this job yet")
    if not (job.application_url or job.job_url):
        reasons.append("the job has no application link")
    if s is None or s.state in AUTOMATION_TERMINAL:
        if not runner["connected"]:
            reasons.append("the CareerPilot Runner is offline on your computer")
        if s is not None and s.state in ("failed", "unsupported", "submitted_unverified"):
            reasons.append(f"the last attempt ended: {s.message}")
        if not reasons:
            return Result(f"Nothing is blocking {_label(job)}. It hasn't been started; say “apply” to start it.", href)
        return Result("Blocked because " + "; ".join(reasons) + ".", href)
    phase = svc.display_phase(s, runner["connected"])
    pending = svc.unresolved(a)
    if phase == "RUNNER_OFFLINE":
        reasons.append("the CareerPilot Runner is offline, so the browser can't continue")
    elif s.state == "action_required":
        reasons.append(f"the browser needs you: {s.message}")
    if pending:
        reasons.append(f"{len(pending)} question(s) need your answer: " + "; ".join(f.label for f in pending[:3]))
    if s.state == "awaiting_review" and not pending:
        reasons.append("it is ready and waiting for your final confirmation")
    if not reasons:
        return Result(f"Nothing is blocking it. The agent is working: {s.message}", href)
    return Result("The application is waiting because " + "; ".join(reasons) + ".", href)


@tool("run_ats_check", READ, "Quality and ATS checks of the current resume", ("resume",))
def run_ats_check(ctx: Ctx) -> Result:
    v = _owned(ctx.db, ResumeVersion, ctx.resume_id, ctx.user_id)
    q = v.quality_report or {}
    counts = q.get("counts") or {}
    checks = {c["id"]: c for c in q.get("checks") or []}
    ats = checks.get("ats_extraction", {}).get("status", "unknown")
    worth = [c["title"] for c in q.get("checks") or [] if c["status"] != "pass"]
    report = (
        f"Resume v{v.version_number}: {v.page_count or '?'} page(s), ATS extraction {ats}. "
        f"{counts.get('pass', 0)} checks pass, {counts.get('review', 0)} to review, {counts.get('fail', 0)} failing."
    )
    if worth:
        report += " Look at: " + "; ".join(worth[:4]) + "."
    return Result(report, f"/resumes/{v.id}")


# --- WRITE --------------------------------------------------------------------------------------------


@tool("generate_resume", WRITE, "Generate a tailored resume for the job", ("job",))
def generate_resume(ctx: Ctx) -> Result:
    from app.services.resumes import start_generation

    job = _job(ctx)
    task_id = start_generation(ctx.db, ctx.user_id, job.id, None)
    return Result(
        f"Generating a tailored resume for {_label(job)}. It appears on the job page when ready.",
        f"/jobs/{job.id}?task={task_id}",
        data={"task_id": task_id},
    )


@tool("undo_resume_change", WRITE, "Undo the last change to the current resume", ("resume",))
def undo_resume_change(ctx: Ctx) -> Result:
    from app.services import editor

    v = _owned(ctx.db, ResumeVersion, ctx.resume_id, ctx.user_id)
    history = editor.payload(ctx.db, v)["history"]
    label = next((h["label"] for h in history if h["current"]), "the last change")
    v = editor.undo(ctx.db, v, None)
    return Result(f"Undid “{label}” on resume v{v.version_number}.", f"/resumes/{v.id}")


@tool("edit_resume", WRITE, "Edit the current resume in natural language", ("resume",))
def edit_resume(ctx: Ctx) -> Result:
    from app.llm.provider import get_provider
    from app.services import resume_agent

    v = _owned(ctx.db, ResumeVersion, ctx.resume_id, ctx.user_id)
    provider = get_provider(ctx.db.get(UserSettings, ctx.user_id))
    out = resume_agent.run(ctx.db, v, ctx.text, ctx.selection, provider, None, confirm=False)
    if out.get("question"):
        return Result(out["question"], f"/resumes/{v.id}", ok=False, data={"options": out.get("options", [])})
    return Result(
        out.get("report") or "No change was needed.", f"/resumes/{v.id}", data={"applied": out.get("applied")}
    )


@tool("add_job_note", WRITE, "Add a note to the job", ("job",))
def add_job_note(ctx: Ctx) -> Result:
    job = _job(ctx)
    text = _payload_text(ctx.text, ("note", "notes", "remember", "jot"))
    if not text:
        return Result("What should the note say?", f"/jobs/{job.id}", ok=False)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    job.notes = ((job.notes or "").rstrip() + f"\n{stamp} — {text}").strip()
    ctx.db.commit()
    return Result(f"Added to the notes for {_label(job)}: “{text}”.", f"/jobs/{job.id}")


@tool("add_career_idea", WRITE, "Save a career-wide idea or note (not tied to one job)")
def add_career_idea(ctx: Ctx) -> Result:
    s = ctx.db.get(UserSettings, ctx.user_id)
    text = _payload_text(ctx.text, ("idea", "ideas", "career idea", "career note"))
    if not text:
        return Result("What's the idea?", "/", ok=False)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    s.notes = ((s.notes or "").rstrip() + f"\n{stamp} — {text}").strip()
    ctx.db.commit()
    return Result(f"Saved to your career ideas: “{text}”.", "/")


@tool("scan_github", WRITE, "Re-read GitHub repositories for evidence")
def scan_github(ctx: Ctx) -> Result:
    from app.tasks.queue import enqueue

    s = ctx.db.get(UserSettings, ctx.user_id)
    if not s.github_username:
        return Result("Connect your GitHub account first (Settings → GitHub).", "/settings?tab=github", ok=False)
    task = enqueue(
        ctx.db,
        ctx.user_id,
        "github_scan",
        {"force": False},
        ["Listing repositories", "Inspecting repositories", "Checking suggested projects"],
    )
    return Result(
        f"Scanning {s.github_username}'s repositories. Unchanged repositories are skipped (cached).",
        "/projects",
        data={"task_id": task.id},
    )


# --- EXTERNAL -----------------------------------------------------------------------------------------


@tool(
    "start_application", EXTERNAL, "Open the employer's application and fill it with the runner", ("job", "application")
)
def start_application(ctx: Ctx) -> Result:
    from app.services import applications as svc

    db = ctx.db
    a = _owned(db, Application, ctx.application_id, ctx.user_id)
    job = _job(ctx)
    href = f"/applications/{a.id}"
    s = svc.latest_session(db, a.id)
    if s is not None and s.state not in AUTOMATION_TERMINAL:
        return continue_application(ctx)
    if a.status == "submitted":
        return Result(f"You already applied to {_label(job)}.", href)
    if not a.resume_version_id:
        latest = db.scalar(
            select(ResumeVersion)
            .where(ResumeVersion.user_id == ctx.user_id, ResumeVersion.job_id == job.id, ResumeVersion.pdf.isnot(None))
            .order_by(ResumeVersion.created_at.desc())
        )
        if latest is None:
            return Result(
                f"There is no resume for {_label(job)} yet. Generate one first.",
                f"/jobs/{job.id}",
                ok=False,
                actions=[{"label": "Generate tailored resume", "command": "generate resume"}],
            )
        a.resume_version_id = latest.id
    svc.start_session(db, a, a.resume_version_id)
    runner = svc.runner_status(db, ctx.user_id)
    if not runner["connected"]:
        return Result(
            f"Application to {_label(job)} is queued, but the CareerPilot Runner isn't connected. Start the runner "
            "and it picks this up automatically.",
            href,
        )
    return Result(f"Started the application to {_label(job)}. The runner is opening the employer's page.", href)


@tool("continue_application", EXTERNAL, "Continue an application after a hand-off", ("job", "application"))
def continue_application(ctx: Ctx) -> Result:
    from app.services import applications as svc

    a = _owned(ctx.db, Application, ctx.application_id, ctx.user_id)
    s = svc.latest_session(ctx.db, a.id)
    href = f"/applications/{a.id}"
    if s is None or s.state in AUTOMATION_TERMINAL:
        return Result("There is no application in progress for this job; say “apply” to start one.", href, ok=False)
    runner = svc.runner_status(ctx.db, ctx.user_id)
    if not runner["connected"]:
        return Result(
            "The CareerPilot Runner is offline, so the browser can't continue. Start the runner; the "
            "application resumes where it stopped.",
            href,
            ok=False,
        )
    pending = svc.unresolved(a)
    if pending and s.state != "action_required":
        return Result(
            f"The agent needs {len(pending)} answer(s) from you first: "
            + "; ".join(f.label for f in pending[:3])
            + ".",
            href,
            ok=False,
        )
    svc.continue_session(ctx.db, a)
    return Result("Told the agent to continue from the current page.", href)


# --- HIGH_IMPACT --------------------------------------------------------------------------------------


@tool(
    "submit_application",
    HIGH_IMPACT,
    "Final submission (always requires explicit confirmation)",
    ("job", "application"),
)
def submit_application(ctx: Ctx) -> Result:
    return Result(
        "Submitting needs your explicit confirmation on the review screen. Nothing was submitted.",
        f"/applications/{ctx.application_id}",
        ok=False,
    )


def _payload_text(text: str, words: tuple[str, ...]) -> str:
    import re

    t = text.strip()
    m = re.search(r"[:\-–—]\s*(.+)$", t)
    if m:
        return m.group(1).strip()
    for w in sorted(words, key=len, reverse=True):
        t2 = re.sub(
            rf"^(?:please\s+)?(?:add|save|make|write|note|remember)?\s*(?:a|an|my|the|this)?\s*{w}\s*(?:that|to|about|for)?\s*",
            "",
            t,
            flags=re.I,
        )
        if t2 != t:
            return t2.strip(" .")
    return ""
