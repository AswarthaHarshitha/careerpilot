"""Builds the project evidence database from the user's GitHub repositories."""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.github.client import GitHubClient, RateLimited, RepoInfo
from app.github.evidence import ANALYZER_VERSION, analyze_repository, repo_summary, select_manifests, select_readme
from app.llm.provider import LLMError, get_provider, wrap_untrusted
from app.matching.taxonomy import canonicalize, category_of, display_name
from app.models import Project, ProjectEvidence, SuggestedProject, UserProfile, UserSettings
from app.services.assets import apply_classification
from app.tasks.queue import RetryLater, TaskContext, task_handler

log = logging.getLogger(__name__)

CACHE_DAYS = 30  # re-read an unchanged repository at most this often


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:190] or "project"


def pretty_name(repo_name: str) -> str:
    """ "resolve-issue-platform" -> "Resolve Issue Platform" (the user can rename it)."""
    if len(repo_name) <= 3 and repo_name.isalpha():
        return repo_name.upper()  # "ci" -> "CI", "api" -> "API"
    words = re.split(r"[-_\s]+", repo_name)
    return " ".join(w if (w.isupper() or any(c.isupper() for c in w[1:])) else w.capitalize() for w in words if w)


def client_for(settings: UserSettings | None) -> GitHubClient:
    token = decrypt_secret(settings.github_token_encrypted) if settings else ""
    return GitHubClient(token=token)


def upsert_repository(
    db: Session,
    user_id: int,
    gh: GitHubClient,
    repo: RepoInfo,
    *,
    force: bool = False,
    settings: UserSettings | None = None,
) -> Project:
    project = db.scalar(select(Project).where(Project.user_id == user_id, Project.repo_full_name == repo.full_name))
    if project is None:
        project = Project(
            user_id=user_id,
            name=repo.name,
            display_name=pretty_name(repo.name),
            origin="github",
            slug=_unique_slug(db, user_id, slugify(repo.name)),
            repo_full_name=repo.full_name,
        )
        db.add(project)
    project.description = repo.description
    project.repo_url = repo.html_url
    project.homepage_url = repo.homepage if repo.homepage.startswith("http") else ""
    project.is_private = repo.private
    project.is_fork = repo.fork
    project.archived = repo.archived
    project.primary_language = repo.language
    project.topics = repo.topics
    project.stars = repo.stars
    fresh = project.last_scanned_at is not None and (datetime.now(UTC) - project.last_scanned_at).days < CACHE_DAYS
    unchanged = (
        fresh
        and project.pushed_at == repo.pushed_at
        and (project.analysis or {}).get("analyzer_version") == ANALYZER_VERSION
        and not project.scan_error
        and not force
    )
    project.pushed_at = repo.pushed_at
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    was_profile_asset = apply_classification(
        project,
        github_username=settings.github_username if settings else "",
        portfolio_url=profile.portfolio_url if profile else "",
    )
    if was_profile_asset and profile and not profile.portfolio_url and project.homepage_url:
        profile.portfolio_url = project.homepage_url  # the user's own site, usable as a header link
    if project.status not in ("built", "verified", "deployed"):
        project.status = "built"
    if project.homepage_url and project.status == "built":
        project.status = "deployed"
    if unchanged:
        db.commit()
        return project

    tree, truncated = gh.tree(repo.full_name, repo.default_branch)
    paths = [t.path for t in tree]
    readme_path = select_readme(paths)
    readme = gh.raw_file(repo.full_name, repo.default_branch, readme_path) if readme_path else None
    manifests = {}
    for path in select_manifests(paths):
        content = gh.raw_file(repo.full_name, repo.default_branch, path)
        if content:
            manifests[path] = content
    analysis = analyze_repository(
        description=repo.description,
        homepage=repo.homepage,
        topics=repo.topics,
        tree=[(t.path, t.size) for t in tree],
        readme=readme,
        manifests=manifests,
    )
    project.languages = analysis.languages
    project.readme_excerpt = analysis.readme_excerpt
    project.file_count = analysis.file_count
    project.analysis = {
        **repo_summary(analysis, manifests),
        "readme_summary": "",
        "scanned_commit_date": (repo.pushed_at.isoformat() if repo.pushed_at else None),
    }
    project.scan_error = "Repository tree was truncated by GitHub; some files were not inspected." if truncated else ""
    db.flush()

    # Replace machine-generated evidence; keep anything the user entered or reviewed.
    db.execute(
        delete(ProjectEvidence).where(
            ProjectEvidence.project_id == project.id,
            ProjectEvidence.source_type.notin_(("user",)),
            ProjectEvidence.user_reviewed.is_(False),
        )
    )
    reviewed = {
        (e.canonical or e.label.lower(), e.source_type)
        for e in db.scalars(select(ProjectEvidence).where(ProjectEvidence.project_id == project.id))
    }
    for item in analysis.evidence:
        if (item.canonical or item.label.lower(), item.source_type) in reviewed:
            continue
        db.add(
            ProjectEvidence(
                project_id=project.id,
                kind=item.kind,
                label=item.label,
                canonical=item.canonical,
                category=item.category,
                status=item.status,
                source_type=item.source_type,
                source_detail=item.source_detail,
                snippet=item.snippet,
            )
        )
    project.last_scanned_at = datetime.now(UTC)
    db.commit()

    if settings is not None:
        enrich_with_llm(db, project, settings)
    return project


def _unique_slug(db: Session, user_id: int, base: str) -> str:
    slug, n = base, 2
    while db.scalar(select(Project.id).where(Project.user_id == user_id, Project.slug == slug)):
        slug = f"{base}-{n}"
        n += 1
    return slug


LLM_SYSTEM = (
    "Read one software repository's README and list the engineering skills and concepts it demonstrates. "
    "Only include items the README clearly describes as implemented in this project. Return JSON: "
    '{"summary": "one factual sentence about what the project is", '
    '"skills": [{"name": "...", "evidence": "short quote or paraphrase from the README"}]} '
    "with at most 12 skills."
)


def enrich_with_llm(db: Session, project: Project, settings: UserSettings) -> None:
    """Optional: a model's reading of the README, stored strictly as INFERRED evidence for the user to review."""
    provider = get_provider(settings)
    if provider is None or not project.readme_excerpt:
        return
    prompt = f"Repository: {project.repo_full_name}\nDescription: {project.description}\n\n" + wrap_untrusted(
        "README", project.readme_excerpt, 8000
    )
    try:
        data = provider.complete_json(LLM_SYSTEM, prompt, max_tokens=1200)
    except LLMError as exc:
        log.info("LLM enrichment skipped for project %s: %s", project.id, exc)
        return
    summary = str(data.get("summary", "")).strip()[:400]
    if summary:
        project.analysis = {**(project.analysis or {}), "readme_summary": summary, "summary_by": provider.used}
    existing = {e.canonical for e in project.evidence if e.canonical}
    added = 0
    for s in (data.get("skills") or [])[:12]:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()[:80]
        key = canonicalize(name)
        if not name or key in existing or key.startswith("x:") and len(name) > 40:
            continue
        existing.add(key)
        db.add(
            ProjectEvidence(
                project_id=project.id,
                kind="concept" if category_of(key) == "concept" else "technology",
                label=display_name(key) if not key.startswith("x:") else name,
                canonical=key,
                category=category_of(key),
                status="inferred",
                source_type="llm",
                source_detail=f"model reading of README ({provider.used or provider.name})",
                snippet=str(s.get("evidence", ""))[:240],
            )
        )
        added += 1
    db.commit()


def scan_account(ctx: TaskContext) -> dict:
    db = ctx.db
    user_id = ctx.task.user_id
    settings = db.get(UserSettings, user_id)
    if settings is None or not settings.github_username:
        from app.core.errors import AppError

        raise AppError("Connect a GitHub username in Settings first.", code="github_not_connected")
    gh = client_for(settings)
    force = bool(ctx.payload.get("force"))
    with ctx.step("Listing repositories"):
        repos = gh.list_repos(settings.github_username, settings.include_private_repos)
        repos = [r for r in repos if settings.include_forks or not r.fork]
    scanned, skipped, failed = 0, 0, []
    label = "Inspecting repositories"
    ctx._set_step(label, "running", f"0 of {len(repos)}")
    for i, repo in enumerate(repos, 1):
        try:
            before = db.scalar(
                select(Project.last_scanned_at).where(
                    Project.user_id == user_id, Project.repo_full_name == repo.full_name
                )
            )
            p = upsert_repository(db, user_id, gh, repo, force=force, settings=settings)
            if before is not None and p.last_scanned_at == before:
                skipped += 1
            else:
                scanned += 1
        except RateLimited as exc:
            db.rollback()
            ctx._set_step(label, "running", f"{i - 1} of {len(repos)} - paused: GitHub rate limit")
            raise RetryLater(exc.message, exc.reset_at.astimezone(UTC) if exc.reset_at else None) from exc
        except Exception as exc:  # noqa: BLE001 - one bad repository must not stop the scan
            db.rollback()
            log.warning("scan of %s failed: %s", repo.full_name, type(exc).__name__)
            failed.append(repo.name)
        ctx._set_step(label, "running", f"{i} of {len(repos)}")
    ctx._set_step(
        label, "done", f"{scanned} analysed, {skipped} unchanged" + (f", {len(failed)} failed" if failed else "")
    )
    with ctx.step("Checking suggested projects"):
        promoted = verify_suggested_projects(db, user_id)
    settings.github_last_scan_at = datetime.now(UTC)
    db.commit()
    return {
        "repositories": len(repos),
        "scanned": scanned,
        "unchanged": skipped,
        "failed": failed,
        "suggested_verified": promoted,
        "rate_remaining": gh.remaining,
    }


task_handler("github_scan")(scan_account)


@task_handler("github_scan_repo")
def scan_single_repo(ctx: TaskContext) -> dict:
    db = ctx.db
    settings = db.get(UserSettings, ctx.task.user_id)
    gh = client_for(settings)
    full_name = ctx.payload["full_name"]
    with ctx.step("Reading repository"):
        repo = gh.get_repo(full_name)
        if repo is None:
            from app.core.errors import NotFound

            raise NotFound("Repository")
    with ctx.step("Extracting evidence"):
        try:
            project = upsert_repository(db, ctx.task.user_id, gh, repo, force=True, settings=settings)
        except RateLimited as exc:
            raise RetryLater(exc.message) from exc
    with ctx.step("Checking suggested projects"):
        promoted = verify_suggested_projects(db, ctx.task.user_id)
    return {"project_id": project.id, "suggested_verified": promoted}


def verify_suggested_projects(db: Session, user_id: int) -> int:
    """A suggested project is verified only when its linked repository shows verified evidence for the gap skills
    it was meant to demonstrate. The per-skill result is stored for display."""
    promoted = 0
    suggestions = db.scalars(
        select(SuggestedProject).where(
            SuggestedProject.user_id == user_id, SuggestedProject.linked_project_id.is_not(None)
        )
    ).all()
    for sp in suggestions:
        project = db.get(Project, sp.linked_project_id)
        if project is None:
            continue
        by_key: dict[str, str] = {}
        for e in project.evidence:
            if e.canonical and e.status in ("verified", "user_provided"):
                by_key.setdefault(e.canonical, e.source_detail or e.source_type)
        wanted = list(dict.fromkeys([g for g in sp.gap_skills if g] + [a for a in sp.existing_skills if a]))
        sp.verification = [
            {
                "skill": k,
                "label": display_name(k),
                "verified": k in by_key,
                "source": by_key.get(k, ""),
                "gap": k in sp.gap_skills,
            }
            for k in wanted
        ]
        missing = [display_name(g) for g in sp.gap_skills if g and g not in by_key]
        if not project.last_scanned_at:
            sp.status = "building"
            sp.verification_notes = "Waiting for the repository scan."
        elif sp.gap_skills and not missing:
            if sp.status != "verified":
                promoted += 1
            sp.status = "deployed" if project.homepage_url else "verified"
            sp.verification_notes = f"Verified from {project.repo_full_name}: " + ", ".join(
                display_name(g) for g in sp.gap_skills
            )
        else:
            sp.status = "built"
            sp.verification_notes = (
                "The repository does not yet show verified evidence for: " + ", ".join(missing)
                if missing
                else "No gap skills recorded."
            )
    db.commit()
    return promoted
