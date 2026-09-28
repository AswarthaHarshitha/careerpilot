"""Resume templates and versions: upload, generation, rendering, review and version history."""

import copy
import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, NotFound
from app.llm.provider import get_provider
from app.matching.taxonomy import display_name
from app.models import (
    Application,
    ApplicationEvent,
    Experience,
    Job,
    JobMatch,
    Project,
    ResumeTemplate,
    ResumeTemplateSection,
    ResumeVersion,
    Skill,
    UserProfile,
    UserSettings,
)
from app.resume import docx_template
from app.resume.latex_compile import check_template_safety, compile_latex, pdf_text
from app.resume.latex_render import RenderError, render_latex
from app.resume.latex_template import TemplateParseError, parse_latex_template
from app.resume.review import ats_check, recruiter_check
from app.resume.tailor import (
    ExperienceInput,
    JobFocus,
    ProjectInput,
    TailorInputs,
    tailor,
    to_render_instructions,
    trim_for_length,
)
from app.tasks.queue import TaskContext, enqueue, task_handler

log = logging.getLogger(__name__)
WEIGHT = {"required": 3.0, "preferred": 1.5, "keyword": 1.0}
MAX_TRIM_STEPS = 8


# --- templates ------------------------------------------------------------------------------------

def create_template(db: Session, user_id: int, filename: str, data: bytes, name: str = "") -> tuple[ResumeTemplate, int]:
    lower = filename.lower()
    if lower.endswith(".tex"):
        try:
            src = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("The .tex file must be UTF-8 text.", code="invalid_template") from exc
        problems = check_template_safety(src)
        if problems:
            raise AppError(problems[0], code="unsafe_template")
        try:
            structure = parse_latex_template(src)
        except TemplateParseError as exc:
            raise AppError(str(exc), code="invalid_template") from exc
        tpl = ResumeTemplate(user_id=user_id, name=name or filename.rsplit(".", 1)[0], format="latex",
                             original_filename=filename, sha256=hashlib.sha256(data).hexdigest(), source_text=src,
                             structure=structure)
    elif lower.endswith(".docx"):
        if not data.startswith(b"PK"):
            raise AppError("That file is not a valid .docx document.", code="invalid_template")
        try:
            structure = docx_template.parse_docx_template(data)
        except docx_template.DocxTemplateError as exc:
            raise AppError(str(exc), code="invalid_template") from exc
        tpl = ResumeTemplate(user_id=user_id, name=name or filename.rsplit(".", 1)[0], format="docx",
                             original_filename=filename, sha256=hashlib.sha256(data).hexdigest(), source_bytes=data,
                             structure=structure)
    elif lower.endswith(".pdf"):
        raise AppError(
            "A PDF can't be used as an editable template.",
            code="pdf_template",
            hint="Upload the source your resume was made from: the .tex file (for example, download it from "
                 "Overleaf via Menu > Download > Source) or a .docx.",
        )
    else:
        raise AppError("Upload a .tex (LaTeX) or .docx resume.", code="invalid_template")

    db.add(tpl)
    db.flush()
    for s in structure["sections"]:
        db.add(ResumeTemplateSection(template_id=tpl.id, position=s["index"], title=s["title"][:200], kind=s["kind"],
                                     entry_count=len(s.get("entries") or s.get("items") or s.get("skills") or [])))
    settings = db.get(UserSettings, user_id)
    if settings and settings.default_template_id is None:
        settings.default_template_id = tpl.id
    db.commit()
    task = enqueue(db, user_id, "template_render", {"template_id": tpl.id}, ["Compiling your template"])
    return tpl, task.id


def _compile(fmt: str, source_text: str | None, source_bytes: bytes | None) -> tuple[bytes | None, int | None, str]:
    if fmt == "latex":
        res = compile_latex(source_text or "")
        return res.pdf, res.page_count, res.error
    pdf, err = docx_template.docx_to_pdf(source_bytes or b"")
    if pdf is None:
        return None, None, err
    from app.resume.latex_compile import count_pages

    return pdf, count_pages(pdf), ""


@task_handler("template_render")
def render_template_task(ctx: TaskContext) -> dict:
    db = ctx.db
    tpl = db.get(ResumeTemplate, ctx.payload["template_id"])
    if tpl is None or tpl.user_id != ctx.task.user_id:
        raise NotFound("Template")
    with ctx.step("Compiling your template"):
        pdf, pages, err = _compile(tpl.format, tpl.source_text, tpl.source_bytes)
        tpl.preview_pdf = pdf
        tpl.page_count = pages
        tpl.render_error = err
        settings = db.get(UserSettings, tpl.user_id)
        if pages and settings and settings.default_template_id == tpl.id and settings.resume_max_pages < pages:
            settings.resume_max_pages = pages  # respect the length of the user's own resume
        db.commit()
    base_exists = db.scalar(select(ResumeVersion.id).where(ResumeVersion.template_id == tpl.id,
                                                           ResumeVersion.is_base.is_(True)))
    if not base_exists:
        db.add(ResumeVersion(user_id=tpl.user_id, template_id=tpl.id, label=f"Original — {tpl.name}", is_base=True,
                             status="approved", content=_content_from_structure(tpl.structure),
                             source_text=tpl.source_text, source_bytes=tpl.source_bytes, pdf=pdf, page_count=pages,
                             render_error=err, approved_at=datetime.now(UTC)))
        db.commit()
    if err:
        return {"error": err}
    return {"pages": pages}


def _content_from_structure(structure: dict) -> dict:
    sections = []
    for s in structure["sections"]:
        base = {"index": s["index"], "title": s["title"], "kind": s["kind"], "layout": s["layout"]}
        if s["layout"] == "paragraph":
            base["text"] = s["paragraph"]["text"]
        elif s["layout"] == "entries":
            base["entries"] = [{"source_index": i, "fields": e["fields"],
                                "bullets": [{"text": it["text"], "original": it["text"], "origin": "template"}
                                            for it in e["items"]] if e["items"] else None}
                               for i, e in enumerate(s["entries"])]
        elif s["layout"] == "skills":
            base["categories"] = [{"name": c["name"], "items": c["items"], "original": c["items"]} for c in s["skills"]]
        elif s["layout"] == "items":
            base["items"] = [it["text"] for it in s["items"]]
        else:
            base["unchanged"] = True
        sections.append(base)
    return {"sections": sections, "focus": [], "used_llm": False}


# --- generation -----------------------------------------------------------------------------------

GENERATE_STEPS = ["Selecting evidence", "Tailoring content", "Rendering your template", "Checking length",
                  "ATS and recruiter review"]


def start_generation(db: Session, user_id: int, job_id: int, template_id: int | None) -> int:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user_id:
        raise NotFound("Job")
    if job.analysis_state != "ready":
        raise AppError("Wait for the job analysis to finish before generating a resume.", code="job_not_ready")
    settings = db.get(UserSettings, user_id)
    tpl_id = template_id or (settings.default_template_id if settings else None)
    tpl = db.get(ResumeTemplate, tpl_id) if tpl_id else None
    if tpl is None or tpl.user_id != user_id:
        raise AppError("Upload your resume template first (Resumes > Templates).", code="no_template",
                       actions=["open_templates"])
    task = enqueue(db, user_id, "resume_generate", {"job_id": job.id, "template_id": tpl.id}, GENERATE_STEPS)
    return task.id


def build_inputs(db: Session, job: Job, tpl: ResumeTemplate) -> TailorInputs:
    user_id = job.user_id
    settings = db.get(UserSettings, user_id)
    weights: dict[str, float] = {}
    ordered: list[str] = []
    for r in job.requirements:
        if r.kind == "skill" and r.canonical:
            weights[r.canonical] = max(weights.get(r.canonical, 0), WEIGHT[r.importance])
            ordered.append(r.canonical)
    ordered.sort(key=lambda k: -weights[k])
    focus = JobFocus(weights, list(dict.fromkeys(ordered)), job.role_title, job.company)

    matches = {m.requirement_id: m for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
    strong = {r.canonical for r in job.requirements if r.canonical and matches.get(r.id)
              and matches[r.id].status == "strong"}

    selected = [rec for rec in job.project_recommendations if rec.selected]
    selected.sort(key=lambda r: r.rank)
    projects = []
    for rec in selected:
        p = db.scalar(select(Project).where(Project.id == rec.project_id, Project.user_id == user_id)
                      .options(selectinload(Project.evidence), selectinload(Project.bullets)))
        if p is None or p.hidden:
            continue
        claimable = {e.canonical for e in p.evidence if e.canonical and e.status in ("verified", "user_provided")}
        tech_keys = [e.canonical for e in p.evidence if e.canonical and e.status in ("verified", "user_provided")
                     and e.kind == "technology" and e.category not in ("concept", "security", "tool")]
        tech_keys = list(dict.fromkeys(sorted(tech_keys, key=lambda k: -weights.get(k, 0))))
        features = [e.label for e in p.evidence if e.kind == "feature" and e.status in ("verified", "user_provided")]
        projects.append(ProjectInput(
            id=p.id, title=p.title, url=p.repo_url or p.homepage_url, description=p.description,
            bullets=[b.text for b in p.bullets], claimable=claimable, tech=[display_name(k) for k in tech_keys],
            features=features, matched=[m["label"] for m in rec.matched], score=rec.score,
        ))
    exps = [ExperienceInput(e.id, e.organization, e.title, [b.text for b in e.bullets])
            for e in db.scalars(select(Experience).where(Experience.user_id == user_id)
                                .options(selectinload(Experience.bullets)))]
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    return TailorInputs(
        structure=tpl.structure, focus=focus, projects=projects, experiences=exps,
        profile_skills=[s.canonical for s in db.scalars(select(Skill).where(Skill.user_id == user_id))],
        strong_skills=strong, profile_summary=profile.summary if profile else "",
        max_bullets_per_project=settings.max_bullets_per_project if settings else 3,
    )


def render_content(tpl: ResumeTemplate, content: dict) -> tuple[str | None, bytes | None, bytes | None, int | None, str]:
    """Returns (latex source, docx bytes, pdf, page count, error)."""
    instructions = to_render_instructions(content)
    if tpl.format == "latex":
        try:
            src = render_latex(tpl.source_text or "", tpl.structure, instructions)
        except RenderError as exc:
            return None, None, None, None, str(exc)
        res = compile_latex(src)
        return src, None, res.pdf, res.page_count, res.error
    try:
        docx = docx_template.render_docx(tpl.source_bytes or b"", tpl.structure, instructions)
    except docx_template.DocxTemplateError as exc:
        return None, None, None, None, str(exc)
    pdf, err = docx_template.docx_to_pdf(docx)
    if pdf is None:
        return None, docx, None, None, err
    from app.resume.latex_compile import count_pages

    return None, docx, pdf, count_pages(pdf), ""


def review(db: Session, job: Job | None, tpl: ResumeTemplate, version: ResumeVersion, max_pages: int) -> None:
    reqs, matches = [], {}
    if job is not None:
        reqs = [{"id": r.id, "kind": r.kind, "label": r.label, "canonical": r.canonical, "importance": r.importance}
                for r in job.requirements]
        matches = {m.requirement_id: m.status for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
    text = pdf_text(version.pdf) if version.pdf else ""
    version.ats_report = ats_check(text, version.content, reqs, matches, version.page_count, max_pages,
                                   tpl.structure.get("header", {}))
    version.recruiter_report = recruiter_check(version.content, reqs, matches, version.page_count, max_pages)


@task_handler("resume_generate")
def generate_task(ctx: TaskContext) -> dict:
    db = ctx.db
    user_id = ctx.task.user_id
    job = db.get(Job, ctx.payload["job_id"])
    tpl = db.get(ResumeTemplate, ctx.payload["template_id"])
    if job is None or tpl is None or job.user_id != user_id or tpl.user_id != user_id:
        raise NotFound("Job or template")
    settings = db.get(UserSettings, user_id)
    max_pages = settings.resume_max_pages if settings else 1

    with ctx.step("Selecting evidence"):
        inputs = build_inputs(db, job, tpl)
    with ctx.step("Tailoring content"):
        content, changes = tailor(inputs, get_provider(settings))
    with ctx.step("Rendering your template"):
        src, docx, pdf, pages, err = render_content(tpl, content)
        if err and pdf is None and docx is None:
            raise AppError(err, code="render_failed", hint="Your template itself may not compile - check the "
                           "template preview on the Templates page.")
    with ctx.step("Checking length"):
        steps = 0
        while pages and pages > max_pages and steps < MAX_TRIM_STEPS:
            trial = copy.deepcopy(content)
            what = trim_for_length(trial, steps)
            if what is None:
                break
            t_src, t_docx, t_pdf, t_pages, t_err = render_content(tpl, trial)
            if t_pdf is None:
                break
            content, src, docx, pdf, pages = trial, t_src, t_docx, t_pdf, t_pages
            changes.append({"section": "Length", "type": "trimmed", "detail": what})
            steps += 1
        if pages and pages > max_pages:
            ctx.note("Checking length", f"{pages} pages (target {max_pages})")
    with ctx.step("ATS and recruiter review"):
        number = (db.scalar(select(func.max(ResumeVersion.version_number)).where(
            ResumeVersion.user_id == user_id, ResumeVersion.job_id == job.id)) or 0) + 1
        label = f"{job.company or 'Company'} — {job.role_title or 'Role'} — {datetime.now(UTC).strftime('%d %b %Y')}"
        version = ResumeVersion(user_id=user_id, job_id=job.id, template_id=tpl.id, version_number=number, label=label,
                                content=content, source_text=src, source_bytes=docx, pdf=pdf, page_count=pages,
                                render_error=err, changes=changes, used_llm=bool(content.get("used_llm")))
        db.add(version)
        db.flush()
        review(db, job, tpl, version, max_pages)
        app_row = db.scalar(select(Application).where(Application.job_id == job.id, Application.user_id == user_id))
        if app_row:
            if app_row.status in ("saved", "analyzing"):
                app_row.status = "resume_ready"
                db.add(ApplicationEvent(application_id=app_row.id, kind="status", message="Resume generated",
                                        data={"to": "resume_ready", "resume_version_id": version.id}))
            if app_row.resume_version_id is None or app_row.status in ("saved", "resume_ready"):
                app_row.resume_version_id = version.id
        db.commit()
    return {"resume_version_id": version.id}


# --- version operations ---------------------------------------------------------------------------

def get_version(db: Session, user_id: int, version_id: int) -> ResumeVersion:
    v = db.get(ResumeVersion, version_id)
    if v is None or v.user_id != user_id:
        raise NotFound("Resume version")
    return v


def rerender_version(db: Session, v: ResumeVersion) -> None:
    tpl = db.get(ResumeTemplate, v.template_id) if v.template_id else None
    if tpl is None:
        raise AppError("The template for this version was deleted, so it can't be re-rendered.", code="no_template")
    src, docx, pdf, pages, err = render_content(tpl, v.content)
    v.source_text, v.source_bytes, v.page_count, v.render_error = src, docx, pages, err
    if pdf is not None:
        v.pdf = pdf
    settings = db.get(UserSettings, v.user_id)
    job = db.get(Job, v.job_id) if v.job_id else None
    review(db, job, tpl, v, settings.resume_max_pages if settings else 1)


def update_content(db: Session, v: ResumeVersion, content: dict) -> ResumeVersion:
    if v.status == "approved" or v.is_base:
        raise AppError("Approved versions can't be edited. Duplicate it to make changes.", code="version_locked",
                       actions=["duplicate"])
    v.content = _sanitize_content(v.content, content)
    v.changes = list(v.changes or []) + [{"section": "All", "type": "edited", "detail": "Edited by you"}]
    rerender_version(db, v)
    db.commit()
    return v


def _sanitize_content(current: dict, incoming: dict) -> dict:
    """Only text fields may change through edits; structure (which template entry is used) stays server-owned."""
    out = copy.deepcopy(current)
    by_index = {s["index"]: s for s in incoming.get("sections", []) if isinstance(s, dict) and "index" in s}
    for sec in out["sections"]:
        new = by_index.get(sec["index"])
        if not new:
            continue
        if sec["layout"] == "paragraph" and isinstance(new.get("text"), str):
            sec["text"] = new["text"][:2000]
        elif sec["layout"] == "entries" and isinstance(new.get("entries"), list):
            keep = []
            for e_new in new["entries"]:
                match = next((e for e in sec["entries"] if e.get("source_index") == e_new.get("source_index")
                              and (e.get("ref") or {}) == (e_new.get("ref") or {})), None)
                if match is None:
                    continue
                if match.get("bullets") is not None and isinstance(e_new.get("bullets"), list):
                    bullets = []
                    for b in e_new["bullets"][:8]:
                        text = (b.get("text") if isinstance(b, dict) else str(b))[:400].strip()
                        if text:
                            orig = b.get("original", text) if isinstance(b, dict) else text
                            origin = "user" if text != orig else (b.get("origin", "user") if isinstance(b, dict)
                                                                  else "user")
                            bullets.append({"text": text, "original": orig, "origin": origin})
                    if bullets:
                        match["bullets"] = bullets
                keep.append(match)
            if keep:
                sec["entries"] = keep
        elif sec["layout"] == "skills" and isinstance(new.get("categories"), list):
            by_name = {c.get("name"): c for c in new["categories"] if isinstance(c, dict)}
            for c in sec["categories"]:
                nc = by_name.get(c["name"])
                if nc and isinstance(nc.get("items"), list):
                    items = [str(i).strip()[:80] for i in nc["items"] if str(i).strip()][:40]
                    if items:
                        c["items"] = items
        elif sec["layout"] == "items" and isinstance(new.get("items"), list):
            items = [str(i).strip()[:400] for i in new["items"] if str(i).strip()][:20]
            if items:
                sec["items"] = items
    return out


def regenerate_section(db: Session, v: ResumeVersion, section_index: int) -> ResumeVersion:
    if v.status == "approved" or v.is_base:
        raise AppError("Approved versions can't be changed. Duplicate it first.", code="version_locked")
    job = db.get(Job, v.job_id) if v.job_id else None
    tpl = db.get(ResumeTemplate, v.template_id) if v.template_id else None
    if job is None or tpl is None:
        raise AppError("This version is not linked to a job and template.", code="cannot_regenerate")
    settings = db.get(UserSettings, v.user_id)
    fresh, changes = tailor(build_inputs(db, job, tpl), get_provider(settings))
    new_sec = next((s for s in fresh["sections"] if s["index"] == section_index), None)
    if new_sec is None:
        raise NotFound("Section")
    content = copy.deepcopy(v.content)
    content["sections"] = [new_sec if s["index"] == section_index else s for s in content["sections"]]
    v.content = content
    v.changes = list(v.changes or []) + [c for c in changes if c.get("section") in (new_sec["title"], "All")]
    rerender_version(db, v)
    db.commit()
    return v


def duplicate_version(db: Session, v: ResumeVersion) -> ResumeVersion:
    number = (db.scalar(select(func.max(ResumeVersion.version_number)).where(
        ResumeVersion.user_id == v.user_id, ResumeVersion.job_id == v.job_id)) or 0) + 1
    dup = ResumeVersion(user_id=v.user_id, job_id=v.job_id, template_id=v.template_id, parent_id=v.id,
                        version_number=number, label=v.label.replace("Original — ", "Copy of "), status="draft",
                        content=copy.deepcopy(v.content), source_text=v.source_text, source_bytes=v.source_bytes,
                        pdf=v.pdf, page_count=v.page_count, ats_report=v.ats_report, recruiter_report=v.recruiter_report,
                        changes=[{"section": "All", "type": "duplicated", "detail": f"Copied from version {v.id}"}],
                        used_llm=v.used_llm)
    db.add(dup)
    db.commit()
    return dup


def compare_versions(a: ResumeVersion, b: ResumeVersion) -> list[dict]:
    """Section-by-section differences between two versions' content."""
    out = []
    sa = {s["index"]: s for s in a.content.get("sections", [])}
    sb = {s["index"]: s for s in b.content.get("sections", [])}
    for idx in sorted(set(sa) | set(sb)):
        x, y = sa.get(idx), sb.get(idx)
        title = (y or x)["title"]
        tx, ty = _section_lines(x), _section_lines(y)
        removed = [t for t in tx if t not in ty]
        added = [t for t in ty if t not in tx]
        if removed or added:
            out.append({"section": title, "removed": removed, "added": added})
    return out


def _section_lines(sec: dict | None) -> list[str]:
    if not sec:
        return []
    if sec.get("layout") == "paragraph":
        return [sec.get("text", "")]
    if sec.get("layout") == "entries":
        lines = []
        for e in sec.get("entries", []):
            lines.append("# " + e.get("fields", {}).get("primary", ""))
            lines += ["• " + b["text"] for b in (e.get("bullets") or [])]
        return lines
    if sec.get("layout") == "skills":
        return [f"{c['name']}: {', '.join(c['items'])}" for c in sec.get("categories", [])]
    if sec.get("layout") == "items":
        return ["• " + t for t in sec.get("items", [])]
    return []
