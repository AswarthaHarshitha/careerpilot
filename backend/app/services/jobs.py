"""Job import, analysis and matching."""

import hashlib
import io
import re
from datetime import UTC, date, datetime, timedelta

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.fetch import validate_url
from app.jobs.llm_extract import enhance_with_llm
from app.jobs.parse import analyze_text
from app.jobs.platforms import RawPosting, detect_platform, fetch_posting
from app.llm.provider import get_provider
from app.matching.engine import RequirementInput, match_requirements, score_projects
from app.models import (
    Application,
    ApplicationEvent,
    Job,
    JobMatch,
    JobProjectRecommendation,
    JobRequirement,
    Reminder,
    UserSettings,
)
from app.services.snapshot import load_snapshot
from app.tasks.queue import TaskContext, enqueue, task_handler

ANALYSIS_STEPS_URL = ["Reading the job page", "Extracting requirements", "Checking your profile", "Analyzing projects",
                      "Preparing recommendations"]
ANALYSIS_STEPS_TEXT = ANALYSIS_STEPS_URL[1:]


def _ensure_application(db: Session, job: Job, status: str) -> Application:
    app_row = db.scalar(select(Application).where(Application.user_id == job.user_id, Application.job_id == job.id))
    if app_row is None:
        app_row = Application(user_id=job.user_id, job_id=job.id, status=status)
        db.add(app_row)
        db.flush()
        db.add(ApplicationEvent(application_id=app_row.id, kind="status", message="Job saved", data={"to": status}))
    return app_row


def create_job_from_url(db: Session, user_id: int, url: str, careers_url: str = "") -> tuple[Job, int]:
    url = validate_url(url)
    if careers_url:
        careers_url = validate_url(careers_url)
    existing = db.scalar(select(Job).where(Job.user_id == user_id, Job.job_url == url, Job.archived.is_(False)))
    if existing:
        raise AppError("You already imported this job.", code="duplicate_job", status_code=409,
                       actions=[f"open_job:{existing.id}"])
    job = Job(user_id=user_id, job_url=url, company_careers_url=careers_url, ingest_method="url",
              source_platform=detect_platform(url), analysis_state="pending")
    db.add(job)
    db.flush()
    _ensure_application(db, job, "analyzing")
    db.commit()
    task = enqueue(db, user_id, "job_analyze", {"job_id": job.id}, ANALYSIS_STEPS_URL)
    return job, task.id


def create_job_from_text(db: Session, user_id: int, text: str, *, company: str = "", role: str = "",
                         job_url: str = "", application_url: str = "", method: str = "paste") -> tuple[Job, int]:
    text = (text or "").strip()
    if len(text) < 120:
        raise AppError("The job description is too short to analyse. Paste the full posting.",
                       code="description_too_short")
    if len(text) > 100_000:
        raise AppError("The job description is too long (over 100,000 characters).", code="description_too_long")
    for u in (job_url, application_url):
        if u:
            validate_url(u)
    job = Job(user_id=user_id, company=company.strip()[:200], role_title=role.strip()[:300], job_url=job_url,
              application_url=application_url, description_text=text, ingest_method=method,
              source_platform=detect_platform(application_url or job_url) if (application_url or job_url) else "pasted",
              analysis_state="pending")
    db.add(job)
    db.flush()
    _ensure_application(db, job, "analyzing")
    db.commit()
    task = enqueue(db, user_id, "job_analyze", {"job_id": job.id}, ANALYSIS_STEPS_TEXT)
    return job, task.id


def text_from_pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF"):
        raise AppError("That file is not a PDF.", code="invalid_file")
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 20:
            raise AppError("The PDF has too many pages for a job description.", code="invalid_file")
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except PdfReadError as exc:
        raise AppError("The PDF could not be read.", code="invalid_file") from exc
    if len(text.strip()) < 120:
        raise AppError("No readable text was found in the PDF (it may be a scanned image).", code="invalid_file")
    return text


@task_handler("job_analyze")
def analyze_job_task(ctx: TaskContext) -> dict:
    db = ctx.db
    job = db.get(Job, ctx.payload["job_id"])
    if job is None or job.user_id != ctx.task.user_id:
        raise AppError("Job not found.", code="not_found")
    job.analysis_state = "running"
    job.analysis_error = ""
    db.commit()
    try:
        return _analyze(ctx, job)
    except AppError as exc:
        db.rollback()
        job = db.get(Job, ctx.payload["job_id"])
        job.analysis_state = "failed"
        job.analysis_error = exc.message + (f" {exc.hint}" if exc.hint else "")
        app_row = db.scalar(select(Application).where(Application.job_id == job.id))
        if app_row and app_row.status == "analyzing":
            app_row.status = "saved"
        db.commit()
        raise


def _analyze(ctx: TaskContext, job: Job) -> dict:
    db = ctx.db
    settings = db.get(UserSettings, job.user_id)
    notes: list[str] = []
    if job.ingest_method == "url" and job.job_url:
        with ctx.step("Reading the job page"):
            posting: RawPosting = fetch_posting(job.job_url)
            job.source_platform = posting.platform
            job.description_text = posting.description_text
            job.role_title = job.role_title or posting.title[:300]
            job.company = job.company or posting.company[:200]
            job.job_code = job.job_code or posting.job_code[:120]
            job.location = posting.location[:300]
            job.workplace_type = posting.workplace_type
            job.employment_type = posting.employment_type[:60]
            job.application_url = posting.application_url or job.job_url
            job.posted_date = posting.posted_date
            if posting.deadline:
                job.deadline = posting.deadline
                job.deadline_source = posting.deadline_source
            notes += posting.notes
            db.commit()

    with ctx.step("Extracting requirements"):
        text = job.description_text
        analysis = analyze_text(text)
        meta = {"company": job.company, "role_title": job.role_title, "location": job.location}
        provider = get_provider(settings)
        if provider is not None:
            notes += enhance_with_llm(provider, text, analysis, meta)
            job.analyzed_with_llm = True
        job.company = job.company or meta.get("company", "") or _guess_company(text)
        job.role_title = job.role_title or meta.get("role_title", "") or _guess_role(text)
        job.location = job.location or analysis.location or meta.get("location", "")
        job.job_code = job.job_code or analysis.job_code
        job.employment_type = job.employment_type or analysis.employment_type
        job.workplace_type = job.workplace_type or analysis.workplace_type
        job.min_years_experience = analysis.min_years
        job.experience_text = analysis.experience_text[:300]
        job.education_text = analysis.education_text[:500]
        if analysis.deadline and not job.deadline:
            job.deadline = analysis.deadline
            job.deadline_source = analysis.deadline_source
        job.responsibilities = analysis.responsibilities
        job.qualifications = analysis.qualifications
        job.keywords = analysis.keywords
        job.content_hash = hashlib.sha256(text.encode()).hexdigest()
        notes += analysis.notes
        job.analysis_notes = notes
        db.execute(delete(JobRequirement).where(JobRequirement.job_id == job.id))
        db.flush()
        for pos, r in enumerate(analysis.requirements):
            db.add(JobRequirement(job_id=job.id, kind=r.kind, label=r.label[:300], canonical=r.canonical,
                                  category=r.category, importance=r.importance, source_snippet=r.snippet[:1000],
                                  position=pos))
        db.commit()
        if not analysis.requirements:
            notes.append("No specific skills were recognised in this posting.")

    with ctx.step("Checking your profile"):
        rematch(db, job, only_requirements=True)
    with ctx.step("Analyzing projects"):
        recommend_projects(db, job)
        auto_supporting_project(db, job, settings)
    with ctx.step("Preparing recommendations"):
        _deadline_reminder(db, job, settings)
        job.analysis_state = "ready"
        app_row = db.scalar(select(Application).where(Application.job_id == job.id))
        if app_row and app_row.status == "analyzing":
            app_row.status = "saved"
            db.add(ApplicationEvent(application_id=app_row.id, kind="status", message="Job analysed",
                                    data={"to": "saved"}))
        db.commit()
    return {"job_id": job.id, "requirements": len(job.requirements)}


def auto_supporting_project(db: Session, job: Job, settings: UserSettings | None) -> None:
    """When the user's finished projects do not cover the job's required skills, prepare one supporting-project
    plan automatically. It is an idea, not experience, and never appears on a resume."""
    from app.llm.provider import get_provider
    from app.models import JobMatch, SuggestedProject
    from app.services.suggestions import coherent_gaps, deterministic_plan, llm_plan

    if db.scalar(select(SuggestedProject.id).where(SuggestedProject.job_id == job.id)):
        return
    matches = {m.requirement_id: m.status for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
    skills = [r for r in job.requirements if r.kind == "skill" and r.canonical and not r.canonical.startswith("x:")]
    missing = [r.canonical for r in skills if r.importance == "required" and matches.get(r.id) == "missing"]
    missing += [r.canonical for r in skills if r.importance == "preferred" and matches.get(r.id) == "missing"]
    required = [r for r in skills if r.importance == "required"]
    covered_by_projects = any(rec.matched for rec in job.project_recommendations)
    if not missing or (covered_by_projects and not any(matches.get(r.id) == "missing" for r in required)):
        return
    gaps = coherent_gaps(list(dict.fromkeys(missing)))
    anchors = [r.canonical for r in skills if matches.get(r.id) == "strong"][:3]
    provider = get_provider(settings)
    plan = llm_plan(provider, gaps, anchors, job.role_title, job.company, job.description_text[:4000]) if provider \
        else None
    plan = plan or deterministic_plan(gaps, anchors, job.role_title, job.company)
    db.add(SuggestedProject(user_id=job.user_id, job_id=job.id, title=plan["title"][:200], gap_skills=gaps,
                            existing_skills=anchors, status="idea", plan=plan, auto_generated=True))
    db.commit()


def _guess_company(text: str) -> str:
    m = re.search(r"\b(?:at|join)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})(?:,|\s+is|\s+we|\.|\s*!)", text)
    return m.group(1)[:200] if m else ""


def _guess_role(text: str) -> str:
    for line in text.splitlines()[:8]:
        line = line.strip("#- ").strip()
        if 3 < len(line) < 90 and re.search(r"engineer|developer|analyst|scientist|intern|manager|architect|"
                                            r"specialist|programmer|consultant|designer|associate", line, re.I):
            return line
    return ""


def rematch(db: Session, job: Job, only_requirements: bool = False) -> None:
    snap = load_snapshot(db, job.user_id)
    reqs = [RequirementInput(r.id, r.kind, r.label, r.canonical, r.importance, r.source_snippet)
            for r in job.requirements]
    db.execute(delete(JobMatch).where(JobMatch.job_id == job.id))
    db.flush()
    for res in match_requirements(reqs, snap):
        db.add(JobMatch(job_id=job.id, requirement_id=res.requirement_id, status=res.status, rationale=res.rationale,
                        evidence_refs=[e.as_dict() for e in res.evidence]))
    db.commit()
    if not only_requirements:
        recommend_projects(db, job, snap)


def recommend_projects(db: Session, job: Job, snap=None) -> None:
    snap = snap or load_snapshot(db, job.user_id)
    reqs = [RequirementInput(r.id, r.kind, r.label, r.canonical, r.importance) for r in job.requirements]
    settings = db.get(UserSettings, job.user_id)
    max_projects = settings.max_projects if settings else 3
    previously_selected = {r.project_id for r in job.project_recommendations if r.selected}
    had_manual = bool(previously_selected)
    db.execute(delete(JobProjectRecommendation).where(JobProjectRecommendation.job_id == job.id))
    db.flush()
    for rank, s in enumerate(score_projects(reqs, snap)[:12], 1):
        selected = (s.project_id in previously_selected) if had_manual else rank <= max_projects
        db.add(JobProjectRecommendation(job_id=job.id, project_id=s.project_id, score=s.score, rank=rank,
                                        matched=s.matched, selected=selected))
    db.commit()
    db.refresh(job)


def _deadline_reminder(db: Session, job: Job, settings: UserSettings | None) -> None:
    db.execute(delete(Reminder).where(Reminder.job_id == job.id, Reminder.kind == "deadline", Reminder.done_at.is_(None)))
    if not job.deadline:
        return
    days = settings.deadline_reminder_days if settings else 3
    due = max(date.today(), job.deadline - timedelta(days=days))
    db.add(Reminder(user_id=job.user_id, job_id=job.id, kind="deadline", due_date=due,
                    message=f"Application deadline for {job.role_title or 'this role'} at {job.company or 'the company'} "
                            f"is {job.deadline.strftime('%d %b %Y')}."))


def days_remaining(deadline: date | None, today: date | None = None) -> int | None:
    if deadline is None:
        return None
    return (deadline - (today or datetime.now(UTC).date())).days
