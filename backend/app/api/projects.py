from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.github.client import parse_repo_url
from app.llm.provider import get_provider
from app.matching.taxonomy import canonicalize, category_of, display_name
from app.models import (
    BackgroundTask,
    Job,
    JobMatch,
    Project,
    ProjectBullet,
    ProjectEvidence,
    SuggestedProject,
    User,
    UserSettings,
)
from app.services.github_scan import slugify
from app.services.suggestions import deterministic_plan, llm_plan
from app.tasks.queue import enqueue

router = APIRouter(prefix="/api", tags=["projects"])


def evidence_out(e: ProjectEvidence) -> dict:
    return {"id": e.id, "kind": e.kind, "label": e.label, "canonical": e.canonical, "category": e.category,
            "status": e.status, "source_type": e.source_type, "source_detail": e.source_detail, "snippet": e.snippet,
            "user_reviewed": e.user_reviewed}


def project_out(p: Project, full: bool = False) -> dict:
    tech = {}
    for e in p.evidence:
        if e.canonical and e.kind in ("technology", "testing") and e.status in ("verified", "user_provided"):
            tech.setdefault(e.canonical, display_name(e.canonical))
    counts = {s: sum(1 for e in p.evidence if e.status == s) for s in ("verified", "user_provided", "inferred",
                                                                        "unsupported")}
    deployment = next((e.label for e in p.evidence if e.kind == "deployment" and e.status != "unsupported"), "")
    out = {
        "id": p.id, "name": p.name, "title": p.title, "display_name": p.display_name, "origin": p.origin,
        "description": p.description, "repo_full_name": p.repo_full_name, "repo_url": p.repo_url,
        "homepage_url": p.homepage_url or deployment, "is_private": p.is_private, "is_fork": p.is_fork,
        "archived": p.archived, "primary_language": p.primary_language, "stars": p.stars, "hidden": p.hidden,
        "pushed_at": p.pushed_at.isoformat() if p.pushed_at else None,
        "last_scanned_at": p.last_scanned_at.isoformat() if p.last_scanned_at else None, "scan_error": p.scan_error,
        "technologies": list(tech.values())[:14], "evidence_counts": counts, "bullet_count": len(p.bullets),
        "topics": p.topics or [],
    }
    if full:
        total = sum((p.languages or {}).values()) or 1
        out.update({
            "evidence": [evidence_out(e) for e in p.evidence],
            "bullets": [{"id": b.id, "text": b.text, "source": b.source} for b in p.bullets],
            "languages": [{"name": k, "share": round(v / total * 100, 1)} for k, v in (p.languages or {}).items()][:8],
            "readme_excerpt": p.readme_excerpt[:1500], "file_count": p.file_count,
        })
    return out


def _owned(db: Session, user_id: int, pid: int) -> Project:
    p = db.scalar(select(Project).where(Project.id == pid, Project.user_id == user_id)
                  .options(selectinload(Project.evidence), selectinload(Project.bullets)))
    if p is None:
        raise NotFound("Project")
    return p


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Project).where(Project.user_id == user.id)
                      .options(selectinload(Project.evidence), selectinload(Project.bullets))
                      .order_by(Project.hidden, Project.pushed_at.desc().nulls_last(), Project.id)).all()
    return [project_out(p) for p in rows]


@router.get("/projects/{pid}")
def get_project(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return project_out(_owned(db, user.id, pid), full=True)


class ProjectIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    homepage_url: str = Field(default="", max_length=300)
    repo_url: str = Field(default="", max_length=300)


@router.post("/projects")
def create_manual_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    base = slugify(body.display_name)
    slug, n = base, 2
    while db.scalar(select(Project.id).where(Project.user_id == user.id, Project.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    p = Project(user_id=user.id, name=body.display_name, display_name=body.display_name, slug=slug, origin="manual",
                description=body.description, homepage_url=body.homepage_url, repo_url=body.repo_url)
    db.add(p)
    db.commit()
    return project_out(_owned(db, user.id, p.id), full=True)


class ProjectPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    homepage_url: str | None = Field(default=None, max_length=300)
    hidden: bool | None = None


@router.patch("/projects/{pid}")
def update_project(pid: int, body: ProjectPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _owned(db, user.id, pid)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    return project_out(p, full=True)


@router.delete("/projects/{pid}")
def delete_project(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _owned(db, user.id, pid)
    if p.origin == "github":
        raise AppError("Projects from GitHub return on the next scan. Hide it instead.", code="use_hide")
    db.delete(p)
    db.commit()
    return {"ok": True}


class BulletsIn(BaseModel):
    bullets: list[str] = Field(max_length=10)


@router.put("/projects/{pid}/bullets")
def set_bullets(pid: int, body: BulletsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _owned(db, user.id, pid)
    p.bullets.clear()
    db.flush()
    p.bullets.extend(ProjectBullet(text=b.strip()[:500], source="user_provided", sort_order=i)
                     for i, b in enumerate(body.bullets) if b.strip())
    db.commit()
    return project_out(p, full=True)


class EvidenceIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["technology", "concept", "feature", "deployment", "testing", "architecture"] = "technology"
    note: str = Field(default="", max_length=500)


@router.post("/projects/{pid}/evidence")
def add_evidence(pid: int, body: EvidenceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _owned(db, user.id, pid)
    key = canonicalize(body.label) if body.kind in ("technology", "concept", "testing") else ""
    db.add(ProjectEvidence(project_id=p.id, kind=body.kind, label=display_name(key) if key and not key.startswith("x:")
                           else body.label.strip(), canonical=key, category=category_of(key) if key else "",
                           status="user_provided", source_type="user", source_detail="added by you",
                           snippet=body.note.strip(), user_reviewed=True))
    db.commit()
    return project_out(_owned(db, user.id, pid), full=True)


class EvidencePatch(BaseModel):
    status: Literal["verified", "user_provided", "inferred", "unsupported"]


@router.patch("/projects/{pid}/evidence/{eid}")
def review_evidence(pid: int, eid: int, body: EvidencePatch, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    p = _owned(db, user.id, pid)
    e = next((x for x in p.evidence if x.id == eid), None)
    if e is None:
        raise NotFound("Evidence")
    if body.status == "verified" and e.source_type in ("llm", "user"):
        # the user vouches for it: that is user-provided evidence, not repository-verified
        e.status = "user_provided"
    else:
        e.status = body.status
    e.user_reviewed = True
    db.commit()
    return evidence_out(e)


@router.delete("/projects/{pid}/evidence/{eid}")
def delete_evidence(pid: int, eid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _owned(db, user.id, pid)
    e = next((x for x in p.evidence if x.id == eid), None)
    if e is None:
        raise NotFound("Evidence")
    if e.source_type != "user":
        e.status, e.user_reviewed = "unsupported", True  # keep it so the next scan does not re-add it
    else:
        db.delete(e)
    db.commit()
    return {"ok": True}


# --- GitHub scanning ------------------------------------------------------------------------------

@router.post("/github/scan")
def scan(body: dict | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = db.get(UserSettings, user.id)
    if not settings.github_username:
        raise AppError("Add your GitHub username in Settings > GitHub first.", code="github_not_connected",
                       actions=["open_settings"])
    running = db.scalar(select(BackgroundTask).where(BackgroundTask.user_id == user.id,
                                                     BackgroundTask.kind == "github_scan",
                                                     BackgroundTask.status.in_(("queued", "running"))))
    if running:
        return {"task_id": running.id}
    task = enqueue(db, user.id, "github_scan", {"force": bool((body or {}).get("force"))},
                   ["Listing repositories", "Inspecting repositories", "Checking suggested projects"])
    return {"task_id": task.id}


class RepoIn(BaseModel):
    repo_url: str = Field(max_length=300)


@router.post("/github/repos")
def add_repo(body: RepoIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    full = parse_repo_url(body.repo_url)
    task = enqueue(db, user.id, "github_scan_repo", {"full_name": full},
                   ["Reading repository", "Extracting evidence", "Checking suggested projects"])
    return {"task_id": task.id}


# --- suggested projects ---------------------------------------------------------------------------

def suggestion_out(s: SuggestedProject) -> dict:
    return {"id": s.id, "title": s.title, "job_id": s.job_id, "gap_skills": [display_name(k) for k in s.gap_skills],
            "gap_keys": s.gap_skills, "existing_skills": [display_name(k) for k in s.existing_skills],
            "status": s.status, "plan": s.plan, "linked_project_id": s.linked_project_id,
            "verification_notes": s.verification_notes, "created_at": s.created_at.isoformat()}


class SuggestIn(BaseModel):
    gap_keys: list[str] = Field(min_length=1, max_length=5)


@router.post("/jobs/{job_id}/suggested-projects")
def suggest(job_id: int, body: SuggestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise NotFound("Job")
    job_keys = {r.canonical for r in job.requirements if r.canonical}
    gaps = [k for k in body.gap_keys if k in job_keys]
    if not gaps:
        raise AppError("Choose requirements from this job.", code="invalid_gaps")
    matches = {m.requirement_id: m.status for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
    anchors = [r.canonical for r in job.requirements if r.canonical and matches.get(r.id) == "strong"][:4]
    provider = get_provider(db.get(UserSettings, user.id))
    plan = None
    if provider is not None:
        plan = llm_plan(provider, gaps, anchors, job.role_title, job.company, job.description_text[:4000])
    plan = plan or deterministic_plan(gaps, anchors, job.role_title, job.company)
    s = SuggestedProject(user_id=user.id, job_id=job.id, title=plan["title"][:200], gap_skills=gaps,
                         existing_skills=anchors, status="not_built", plan=plan)
    db.add(s)
    db.commit()
    return suggestion_out(s)


@router.get("/suggested-projects")
def list_suggestions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [suggestion_out(s) for s in db.scalars(select(SuggestedProject).where(SuggestedProject.user_id == user.id)
                                                  .order_by(SuggestedProject.created_at.desc()))]


def _owned_suggestion(db: Session, user_id: int, sid: int) -> SuggestedProject:
    s = db.get(SuggestedProject, sid)
    if s is None or s.user_id != user_id:
        raise NotFound("Suggested project")
    return s


@router.get("/suggested-projects/{sid}")
def get_suggestion(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return suggestion_out(_owned_suggestion(db, user.id, sid))


class SuggestionPatch(BaseModel):
    status: Literal["not_built", "in_progress"]


@router.patch("/suggested-projects/{sid}")
def update_suggestion(sid: int, body: SuggestionPatch, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    s = _owned_suggestion(db, user.id, sid)
    if s.status == "verified":
        raise AppError("This project is already verified from its repository.", code="already_verified")
    s.status = body.status
    db.commit()
    return suggestion_out(s)


@router.post("/suggested-projects/{sid}/link")
def link_suggestion(sid: int, body: RepoIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Links the repository the user built. Verification happens after the repository is scanned."""
    s = _owned_suggestion(db, user.id, sid)
    full = parse_repo_url(body.repo_url)
    existing = db.scalar(select(Project).where(Project.user_id == user.id, Project.repo_full_name.ilike(full)))
    if existing is None:
        existing = Project(user_id=user.id, name=full.split("/")[-1], display_name=s.title, origin="github",
                           slug=slugify(full.split("/")[-1]) + f"-{s.id}", repo_full_name=full,
                           repo_url=f"https://github.com/{full}")
        db.add(existing)
        db.flush()
    s.linked_project_id = existing.id
    s.status = "in_progress"
    s.verification_notes = "Scanning the repository..."
    db.commit()
    task = enqueue(db, user.id, "github_scan_repo", {"full_name": full},
                   ["Reading repository", "Extracting evidence", "Checking suggested projects"])
    return {"suggestion": suggestion_out(s), "task_id": task.id}


@router.delete("/suggested-projects/{sid}")
def delete_suggestion(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned_suggestion(db, user.id, sid))
    db.commit()
    return {"ok": True}
