from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user
from app.api.templates import read_upload
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.fetch import validate_url
from app.core.ratelimit import limit
from app.models import (
    Application,
    Job,
    JobMatch,
    JobProjectRecommendation,
    Project,
    ResumeVersion,
    SuggestedProject,
    User,
)
from app.services import jobs as svc
from app.tasks.queue import enqueue

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobUrlIn(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=100_000)
    company: str = Field(default="", max_length=200)
    role_title: str = Field(default="", max_length=300)
    careers_url: str = Field(default="", max_length=2000)
    application_url: str = Field(default="", max_length=2000)
    job_code: str = Field(default="", max_length=120)
    resume_instructions: "InstructionsIn | None" = None
    notes: str = Field(default="", max_length=10_000)


class InstructionsIn(BaseModel):
    controls: dict = Field(default_factory=dict)
    custom: list[str] = Field(default_factory=list, max_length=40)

    def clean(self) -> dict:
        allowed = {
            "portfolio": ("default", "include", "exclude"),
            "github": ("default", "include", "exclude"),
            "linkedin": ("default", "include", "exclude"),
            "length": ("default", 1, 2, "1", "2"),
            "projects": ("auto", 2, 3, 4, "2", "3", "4"),
            "summary": ("auto", "include", "exclude"),
            "certifications": ("auto", "include", "exclude"),
        }
        controls = {}
        for k, v in (self.controls or {}).items():
            if k == "template_id" and (v is None or isinstance(v, int)):
                controls[k] = v
            elif k in allowed and v in allowed[k]:
                controls[k] = int(v) if isinstance(v, str) and v.isdigit() else v
        custom = [c.strip()[:500] for c in self.custom if isinstance(c, str) and c.strip()]
        return {"controls": controls, "custom": custom}


JobUrlIn.model_rebuild()


def job_summary(j: Job, app_row: Application | None = None, counts: dict | None = None) -> dict:
    return {
        "id": j.id,
        "company": j.company,
        "role_title": j.role_title,
        "job_code": j.job_code,
        "location": j.location,
        "workplace_type": j.workplace_type,
        "employment_type": j.employment_type,
        "deadline": j.deadline.isoformat() if j.deadline else None,
        "deadline_source": j.deadline_source,
        "days_remaining": svc.days_remaining(j.deadline),
        "source_platform": j.source_platform,
        "analysis_state": j.analysis_state,
        "analysis_error": j.analysis_error,
        "created_at": j.created_at.isoformat(),
        "job_url": j.job_url,
        "application_url": j.application_url,
        "archived": j.archived,
        "application": {"id": app_row.id, "status": app_row.status} if app_row else None,
        "match_counts": counts or {},
    }


def _match_counts(db: Session, job_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not job_ids:
        return out
    from app.models import JobRequirement

    rows = db.execute(
        select(JobMatch.job_id, JobMatch.status, JobRequirement.importance)
        .join(JobRequirement, JobRequirement.id == JobMatch.requirement_id)
        .where(JobMatch.job_id.in_(job_ids))
    ).all()
    for job_id, status, importance in rows:
        c = out.setdefault(
            job_id,
            {
                "strong": 0,
                "partial": 0,
                "missing": 0,
                "needs_verification": 0,
                "required_total": 0,
                "required_strong": 0,
            },
        )
        c[status] += 1
        if importance == "required":
            c["required_total"] += 1
            if status == "strong":
                c["required_strong"] += 1
    return out


@router.post("")
def import_job(body: JobUrlIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    limit(request, "job_import", 30, 60)
    if body.description and body.description.strip():
        job, task_id = svc.create_job_from_text(
            db,
            user.id,
            body.description,
            company=body.company,
            role=body.role_title,
            job_url=(body.url or "").strip(),
            application_url=body.application_url.strip(),
        )
    elif body.url:
        job, task_id = svc.create_job_from_url(db, user.id, body.url, body.careers_url.strip())
    else:
        raise AppError("Paste a job URL or the job description.", code="missing_input")
    if body.job_code:
        job.job_code = body.job_code.strip()
    if body.resume_instructions:
        job.resume_instructions = body.resume_instructions.clean()
    job.notes = body.notes.strip()
    db.commit()
    return {"job": job_summary(job), "task_id": task_id}


@router.post("/pdf")
async def import_pdf(
    file: UploadFile = File(...),
    company: str = Form(default=""),
    role_title: str = Form(default=""),
    application_url: str = Form(default=""),
    instructions: str = Form(default=""),
    notes: str = Form(default=""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    import json as _json

    data = await read_upload(file, 5_000_000)
    text = svc.text_from_pdf(data)
    job, task_id = svc.create_job_from_text(
        db, user.id, text, company=company, role=role_title, application_url=application_url.strip(), method="pdf"
    )
    if instructions:
        try:
            job.resume_instructions = InstructionsIn(**_json.loads(instructions)).clean()
        except (ValueError, TypeError) as exc:
            raise AppError("The resume instructions could not be read.", code="invalid_instructions") from exc
    job.notes = notes.strip()[:10_000]
    db.commit()
    return {"job": job_summary(job), "task_id": task_id}


@router.get("")
def list_jobs(archived: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(Job).where(Job.user_id == user.id, Job.archived.is_(archived)).order_by(Job.created_at.desc()).limit(500)
    ).all()
    apps = {a.job_id: a for a in db.scalars(select(Application).where(Application.user_id == user.id))}
    counts = _match_counts(db, [j.id for j in jobs])
    return [job_summary(j, apps.get(j.id), counts.get(j.id)) for j in jobs]


def _owned(db: Session, user_id: int, job_id: int) -> Job:
    j = db.scalar(
        select(Job)
        .where(Job.id == job_id, Job.user_id == user_id)
        .options(selectinload(Job.requirements), selectinload(Job.project_recommendations))
    )
    if j is None:
        raise NotFound("Job")
    return j


@router.get("/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = _owned(db, user.id, job_id)
    matches = {m.requirement_id: m for m in db.scalars(select(JobMatch).where(JobMatch.job_id == j.id))}
    reqs = []
    for r in j.requirements:
        m = matches.get(r.id)
        reqs.append(
            {
                "id": r.id,
                "kind": r.kind,
                "label": r.label,
                "canonical": r.canonical,
                "category": r.category,
                "importance": r.importance,
                "snippet": r.source_snippet,
                "match": {"status": m.status, "rationale": m.rationale, "evidence": m.evidence_refs} if m else None,
            }
        )
    projects = {
        p.id: p
        for p in db.scalars(
            select(Project).where(
                Project.id.in_([r.project_id for r in j.project_recommendations]), Project.user_id == user.id
            )
        )
    }
    recs = [
        {
            "project_id": r.project_id,
            "title": projects[r.project_id].title,
            "score": r.score,
            "rank": r.rank,
            "matched": r.matched,
            "selected": r.selected,
            "repo_url": projects[r.project_id].repo_url,
            "has_bullets": bool(projects[r.project_id].bullets),
        }
        for r in j.project_recommendations
        if r.project_id in projects
    ]
    app_row = db.scalar(select(Application).where(Application.job_id == j.id, Application.user_id == user.id))
    resumes = db.scalars(
        select(ResumeVersion)
        .where(ResumeVersion.job_id == j.id, ResumeVersion.user_id == user.id)
        .order_by(ResumeVersion.created_at.desc())
    ).all()
    suggestions = db.scalars(
        select(SuggestedProject).where(SuggestedProject.job_id == j.id, SuggestedProject.user_id == user.id)
    ).all()
    counts = _match_counts(db, [j.id]).get(j.id, {})
    out = job_summary(j, app_row, counts)
    out.update(
        {
            "description_text": j.description_text,
            "responsibilities": j.responsibilities,
            "qualifications": j.qualifications,
            "keywords": j.keywords,
            "experience_text": j.experience_text,
            "min_years_experience": j.min_years_experience,
            "education_text": j.education_text,
            "posted_date": j.posted_date.isoformat() if j.posted_date else None,
            "analysis_notes": j.analysis_notes,
            "analyzed_with_llm": j.analyzed_with_llm,
            "company_careers_url": j.company_careers_url,
            "ingest_method": j.ingest_method,
            "requirements": reqs,
            "recommended_projects": recs,
            "resumes": [
                {
                    "id": v.id,
                    "label": v.label,
                    "version_number": v.version_number,
                    "status": v.status,
                    "created_at": v.created_at.isoformat(),
                    "page_count": v.page_count,
                }
                for v in resumes
            ],
            "suggested_projects": [
                {"id": s.id, "title": s.title, "status": s.status, "auto_generated": s.auto_generated}
                for s in suggestions
            ],
            "resume_instructions": j.resume_instructions or {"controls": {}, "custom": []},
            "notes": j.notes,
            "resume_config": _config_preview(db, j, user.id),
        }
    )
    return out


def _config_preview(db: Session, j: Job, user_id: int) -> dict | None:
    from app.core.errors import AppError as _AppError
    from app.models import UserSettings
    from app.services.resumes import job_config, resolve_template

    settings = db.get(UserSettings, user_id)
    try:
        tpl = resolve_template(db, user_id, j, None)
    except _AppError:
        tpl = None
    cfg = job_config(db, j, tpl, settings)
    return {
        "template": tpl.name if tpl else None,
        "template_id": tpl.id if tpl else None,
        "length": cfg["length"],
        "projects": cfg["projects"],
        "links": cfg["links"],
        "summary": cfg["summary"],
        "certifications": cfg["certifications"],
        "custom_count": cfg["custom_count"],
        "instructions": cfg["summary_lines"],
        "conflicts": cfg["conflicts"],
        "unrecognized": cfg["unrecognized"],
        "ambiguous": cfg["ambiguous"],
        "applied": cfg["applied"],
    }


class JobPatch(BaseModel):
    company: str | None = Field(default=None, max_length=200)
    role_title: str | None = Field(default=None, max_length=300)
    job_code: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=300)
    application_url: str | None = Field(default=None, max_length=2000)
    deadline: date | None = None
    clear_deadline: bool = False
    archived: bool | None = None
    resume_instructions: InstructionsIn | None = None
    notes: str | None = Field(default=None, max_length=10_000)

    @field_validator("application_url")
    @classmethod
    def _v(cls, v):
        return validate_url(v) if v else v


@router.patch("/{job_id}")
def update_job(job_id: int, body: JobPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = _owned(db, user.id, job_id)
    data = body.model_dump(exclude_none=True, exclude={"clear_deadline", "deadline", "resume_instructions"})
    if body.resume_instructions is not None:
        j.resume_instructions = body.resume_instructions.clean()
    for k, v in data.items():
        setattr(j, k, v.strip() if isinstance(v, str) else v)
    if body.clear_deadline:
        j.deadline, j.deadline_source = None, ""
    elif body.deadline:
        j.deadline, j.deadline_source = body.deadline, "Entered by you"
    if body.deadline or body.clear_deadline:
        from app.models import UserSettings

        svc._deadline_reminder(db, j, db.get(UserSettings, user.id))
    db.commit()
    return get_job(job_id, user, db)


@router.post("/{job_id}/reanalyze")
def reanalyze(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = _owned(db, user.id, job_id)
    j.analysis_state = "pending"
    db.commit()
    steps = svc.ANALYSIS_STEPS_URL if j.ingest_method == "url" else svc.ANALYSIS_STEPS_TEXT
    task = enqueue(db, user.id, "job_analyze", {"job_id": j.id}, steps)
    return {"task_id": task.id}


@router.post("/{job_id}/rematch")
def rematch(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = _owned(db, user.id, job_id)
    svc.rematch(db, j)
    return get_job(job_id, user, db)


class SelectionIn(BaseModel):
    project_ids: list[int] = Field(max_length=8)


@router.put("/{job_id}/projects")
def select_projects(job_id: int, body: SelectionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = _owned(db, user.id, job_id)
    wanted = set(body.project_ids)
    owned = set(db.scalars(select(Project.id).where(Project.user_id == user.id, Project.id.in_(wanted))))
    if owned != wanted:
        raise NotFound("Project")
    existing = {r.project_id: r for r in j.project_recommendations}
    for pid in wanted - set(existing):
        rec = JobProjectRecommendation(job_id=j.id, project_id=pid, score=0, rank=99, matched=[], selected=True)
        db.add(rec)
    for pid, r in existing.items():
        r.selected = pid in wanted
    db.commit()
    return get_job(job_id, user, db)


@router.delete("/{job_id}")
def archive_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = _owned(db, user.id, job_id)
    j.archived = True
    db.commit()
    return {"ok": True}


class ParseIn(BaseModel):
    text: str = Field(max_length=5000)
    job_id: int | None = None


@router.post("/instructions/parse")
def parse_instructions(body: ParseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Shows how free-text instructions will be interpreted, before they are saved or applied."""
    from app.resume.instructions import parse, split_lines
    from app.services.resumes import eligible_project_names

    interp = parse(split_lines(body.text), eligible_project_names(db, user.id))
    return {
        "applied": interp.applied,
        "controls": interp.controls,
        "project_priority": interp.project_priority,
        "emphasis": interp.emphasis,
        "flags": sorted(interp.flags),
        "conflicts": interp.conflicts,
        "unrecognized": interp.unrecognized,
        "ambiguous": interp.ambiguous,
        "lines": split_lines(body.text),
    }
