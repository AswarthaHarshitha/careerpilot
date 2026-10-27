"""Resume templates and versions: upload, generation, rendering, review and version history."""

import copy
import hashlib
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, NotFound
from app.llm.provider import get_provider
from app.llm.router import describe
from app.matching.taxonomy import display_name
from app.models import (
    Achievement,
    Application,
    ApplicationEvent,
    Certification,
    Education,
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
from app.resume import docx_template, house_template, latex_project, pdf_template
from app.resume.document import ensure_ids, user_claims
from app.resume.instructions import effective_config, summary_lines
from app.resume.latex_compile import check_template_safety, compile_latex, pdf_text
from app.resume.latex_render import RenderError, render_latex
from app.resume.latex_template import TemplateParseError, parse_latex_template
from app.resume.quality import (
    PROFILE_ASSET_NAME,
    Check,
    evidence_checks,
    instruction_checks,
    language_checks,
    pdf_checks,
    stuffing_check,
    summarize,
)
from app.resume.review import ats_check, recruiter_check
from app.resume.tailor import (
    CERT_ITEM,
    ExperienceInput,
    JobFocus,
    ProjectInput,
    TailorInputs,
    polish,
    removed_sections,
    section_titles,
    tailor,
    to_render_instructions,
    trim_for_length,
)
from app.resume.text_template import text_to_latex
from app.services.assets import is_personal_profile
from app.tasks.queue import TaskContext, enqueue, task_handler

log = logging.getLogger(__name__)
WEIGHT = {"required": 3.0, "preferred": 1.5, "keyword": 1.0}
MAX_TRIM_STEPS = 8


# --- templates ------------------------------------------------------------------------------------

TEMPLATE_TYPES = ".tex, .zip (Overleaf project), .docx, .pdf, .txt or .md"


def create_template(
    db: Session, user_id: int, filename: str, data: bytes, name: str = ""
) -> tuple[ResumeTemplate, int]:
    lower = filename.lower()
    base_name = name or filename.rsplit(".", 1)[0]
    sha = hashlib.sha256(data).hexdigest()
    if lower.endswith(".tex") or lower.endswith(".zip"):
        aux: dict[str, str] = {}
        if lower.endswith(".zip"):
            src, aux = latex_project.from_zip(data)
        else:
            try:
                src = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AppError("The .tex file must be UTF-8 text.", code="invalid_template") from exc
        problems = check_template_safety(src) + [p for a in aux.values() for p in check_template_safety(a)]
        if problems:
            raise AppError(problems[0], code="unsafe_template")
        try:
            structure = parse_latex_template(src)
        except TemplateParseError as exc:
            raise AppError(str(exc), code="invalid_template") from exc
        if aux:
            structure["aux_files"] = aux
        tpl = ResumeTemplate(
            user_id=user_id,
            name=base_name,
            format="latex",
            original_filename=filename,
            sha256=sha,
            source_text=src,
            structure=structure,
        )
    elif lower.endswith(".docx"):
        if not data.startswith(b"PK"):
            raise AppError("That file is not a valid .docx document.", code="invalid_template")
        try:
            structure = docx_template.parse_docx_template(data)
        except docx_template.DocxTemplateError as exc:
            raise AppError(str(exc), code="invalid_template") from exc
        tpl = ResumeTemplate(
            user_id=user_id,
            name=base_name,
            format="docx",
            original_filename=filename,
            sha256=sha,
            source_bytes=data,
            structure=structure,
        )
    elif lower.endswith(".pdf"):
        if not data.startswith(b"%PDF"):
            raise AppError("That file is not a PDF.", code="invalid_template")
        # The PDF is analysed and rebuilt in the background; its structure is filled in by the render task.
        structure = {"format": "pdf", "sections": [], "header": {}, "analysis_pending": True}
        tpl = ResumeTemplate(
            user_id=user_id,
            name=base_name,
            format="pdf",
            original_filename=filename,
            sha256=sha,
            source_bytes=data,
            structure=structure,
        )
    elif lower.endswith((".txt", ".md", ".markdown")):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("The text file must be UTF-8.", code="invalid_template") from exc
        try:
            src = text_to_latex(text)
            structure = parse_latex_template(src)
        except (ValueError, TemplateParseError) as exc:
            raise AppError(f"The text resume could not be read: {exc}", code="invalid_template") from exc
        tpl = ResumeTemplate(
            user_id=user_id,
            name=base_name,
            format="text",
            original_filename=filename,
            sha256=sha,
            source_text=src,
            source_bytes=data,
            structure=structure,
        )
    else:
        raise AppError(f"Upload a resume as {TEMPLATE_TYPES}.", code="invalid_template")
    return _save_template(db, user_id, tpl)


def _save_template(db: Session, user_id: int, tpl: ResumeTemplate) -> tuple[ResumeTemplate, int]:
    db.add(tpl)
    db.flush()
    _sync_sections(db, tpl)
    settings = db.get(UserSettings, user_id)
    if settings and settings.default_template_id is None:
        settings.default_template_id = tpl.id
    db.commit()
    steps = (
        ["Analysing the PDF layout", "Rebuilding it as an editable template", "Compiling your template"]
        if tpl.format == "pdf"
        else ["Compiling your template"]
    )
    task = enqueue(db, user_id, "template_render", {"template_id": tpl.id}, steps)
    return tpl, task.id


def _sync_sections(db: Session, tpl: ResumeTemplate) -> None:
    for old in list(tpl.sections):
        db.delete(old)
    db.flush()
    for s in tpl.structure.get("sections", []):
        db.add(
            ResumeTemplateSection(
                template_id=tpl.id,
                position=s["index"],
                title=s["title"][:200],
                kind=s["kind"],
                entry_count=len(s.get("entries") or s.get("items") or s.get("skills") or s.get("rows") or []),
            )
        )


def create_template_from_profile(db: Session, user_id: int) -> tuple[ResumeTemplate, int]:
    """Builds a template in CareerPilot's standard layout from the career profile."""
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None or not profile.full_name:
        raise AppError("Add your name and contact details to the career profile first.", code="profile_incomplete")
    exps = db.scalars(
        select(Experience)
        .where(Experience.user_id == user_id)
        .options(selectinload(Experience.bullets))
        .order_by(Experience.sort_order, Experience.id)
    ).all()
    projects = [
        p
        for p in db.scalars(
            select(Project)
            .where(Project.user_id == user_id)
            .options(selectinload(Project.bullets), selectinload(Project.evidence))
        )
        if p.evidence_eligible and p.bullets
    ][:4]
    skills: dict[str, list[str]] = {}
    for sk in db.scalars(select(Skill).where(Skill.user_id == user_id).order_by(Skill.sort_order, Skill.id)):
        skills.setdefault(sk.category or "Skills", []).append(sk.name)
    edus = db.scalars(select(Education).where(Education.user_id == user_id).order_by(Education.sort_order)).all()
    certs = [c.name for c in db.scalars(select(Certification).where(Certification.user_id == user_id))]
    achievements = [a.text for a in db.scalars(select(Achievement).where(Achievement.user_id == user_id))]

    def tech_of(p: Project) -> list[str]:
        keys = [
            e.canonical
            for e in p.evidence
            if e.canonical
            and e.status == "verified"
            and e.kind == "technology"
            and e.category not in ("concept", "security", "tool")
        ]
        return [display_name(k) for k in list(dict.fromkeys(keys))[:6]]

    src = house_template.build(
        {
            "full_name": profile.full_name,
            "email": profile.email,
            "phone": profile.phone,
            "github_url": profile.github_url,
            "linkedin_url": profile.linkedin_url,
            "portfolio_url": "",
            "summary": profile.summary,
        },
        [
            {
                "organization": e.organization,
                "dates": " -- ".join(x for x in (e.start_date, "Present" if e.is_current else e.end_date) if x),
                "title": e.title,
                "location": e.location,
                "bullets": [b.text for b in e.bullets],
                "tech": [],
            }
            for e in exps
        ],
        [
            {
                "title": p.title,
                "url": p.repo_url or p.homepage_url,
                "bullets": [b.text for b in p.bullets],
                "tech": tech_of(p),
            }
            for p in projects
        ],
        skills,
        [
            {
                "institution": e.institution,
                "degree": e.degree,
                "start": e.start_date,
                "end": e.end_date,
                "year": e.graduation_year,
                "grade": e.grade,
            }
            for e in edus
        ],
        certs,
        achievements,
    )
    structure = parse_latex_template(src)
    tpl = ResumeTemplate(
        user_id=user_id,
        name="Standard layout (from profile)",
        format="latex",
        original_filename="standard-layout.tex",
        sha256=hashlib.sha256(src.encode()).hexdigest(),
        source_text=src,
        structure=structure,
    )
    return _save_template(db, user_id, tpl)


def _compile(
    fmt: str, source_text: str | None, source_bytes: bytes | None, aux: dict | None = None
) -> tuple[bytes | None, int | None, str]:
    if fmt in ("latex", "pdf", "text"):
        res = compile_latex(source_text or "", aux)
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
    if tpl.format == "pdf" and not tpl.source_text:
        with ctx.step("Analysing the PDF layout"):
            try:
                analysis = pdf_template.analyse(tpl.source_bytes or b"")
            except pdf_template.PdfTemplateError as exc:
                tpl.render_error = str(exc)
                db.commit()
                raise AppError(str(exc), code="pdf_unreadable") from exc
        with ctx.step("Rebuilding it as an editable template"):
            src, pdf, comparison, err = pdf_template.calibrate(analysis, lambda t: compile_latex(t))
            if err or pdf is None:
                tpl.render_error = err or "The rebuilt template did not compile."
                db.commit()
                raise AppError(tpl.render_error, code="pdf_rebuild_failed")
            structure = parse_latex_template(src)
            structure["fingerprint"] = analysis["fingerprint"]
            structure["comparison"] = comparison
            structure["original_heading_positions"] = analysis["heading_positions"]
            tpl.source_text = src
            tpl.structure = structure
            _sync_sections(db, tpl)
            db.commit()
    with ctx.step("Compiling your template"):
        pdf, pages, err = _compile(tpl.format, tpl.source_text, tpl.source_bytes, tpl.structure.get("aux_files"))
        tpl.preview_pdf = pdf
        tpl.page_count = pages
        tpl.render_error = err
        settings = db.get(UserSettings, tpl.user_id)
        if pages and settings and settings.default_template_id == tpl.id and settings.resume_max_pages < pages:
            settings.resume_max_pages = pages  # respect the length of the user's own resume
        db.commit()
    base_exists = db.scalar(
        select(ResumeVersion.id).where(ResumeVersion.template_id == tpl.id, ResumeVersion.is_base.is_(True))
    )
    if not base_exists:
        db.add(
            ResumeVersion(
                user_id=tpl.user_id,
                template_id=tpl.id,
                label=f"Original — {tpl.name}",
                is_base=True,
                status="approved",
                content=_content_from_structure(tpl.structure),
                source_text=tpl.source_text,
                source_bytes=tpl.source_bytes if tpl.format == "docx" else None,
                pdf=pdf,
                page_count=pages,
                render_error=err,
                approved_at=datetime.now(UTC),
            )
        )
        db.commit()
    if err:
        return {"error": err}
    return {"pages": pages, "comparison": tpl.structure.get("comparison")}


def _content_from_structure(structure: dict) -> dict:
    sections = []
    for s in structure["sections"]:
        base = {"index": s["index"], "title": s["title"], "kind": s["kind"], "layout": s["layout"]}
        if s["layout"] == "paragraph":
            base["text"] = s["paragraph"]["text"]
        elif s["layout"] == "entries":
            base["entries"] = [
                {
                    "source_index": i,
                    "fields": e["fields"],
                    "bullets": [{"text": it["text"], "original": it["text"], "origin": "template"} for it in e["items"]]
                    if e["items"]
                    else None,
                }
                for i, e in enumerate(s["entries"])
            ]
        elif s["layout"] == "skills":
            base["categories"] = [{"name": c["name"], "items": c["items"], "original": c["items"]} for c in s["skills"]]
        elif s["layout"] == "items":
            base["items"] = [it["text"] for it in s["items"]]
        else:
            base["unchanged"] = True
        sections.append(base)
    return {"sections": sections, "focus": [], "used_llm": False}


# --- generation -----------------------------------------------------------------------------------

GENERATE_STEPS = [
    "Selecting evidence",
    "Applying your instructions",
    "Tailoring content",
    "Rendering your template",
    "Fitting the page limit",
    "Quality and evidence checks",
]
FIT_MARK = "% careerpilot-fit"


GENERIC_FIRST_WORDS = {
    "personal",
    "online",
    "simple",
    "smart",
    "real",
    "time",
    "full",
    "stack",
    "student",
    "employee",
    "customer",
    "enterprise",
    "cloud",
    "automated",
    "intelligent",
    "basic",
    "advanced",
    "web",
    "mobile",
}


def eligible_project_names(db: Session, user_id: int) -> list[str]:
    names = []
    for p in db.scalars(select(Project).where(Project.user_id == user_id)):
        if p.evidence_eligible:
            names += [p.title, p.name]
            short = re.split(r"\s+[-–—]\s+|:", p.title)[0].strip()
            if short and short not in names:
                names.append(short)
            first = re.split(r"[\s_-]+", p.name or p.title)[0]
            if len(first) >= 4 and first.lower() not in GENERIC_FIRST_WORDS:
                names.append(first.capitalize() if first.islower() else first)
    return [n for n in dict.fromkeys(names) if n]


def job_config(db: Session, job: Job, tpl: ResumeTemplate | None, settings: UserSettings | None) -> dict:
    cfg = effective_config(
        job.resume_instructions or {},
        settings,
        tpl.page_count if tpl else None,
        eligible_project_names(db, job.user_id),
        tpl.name if tpl else "",
    )
    if cfg.get("reuse_order_from"):
        prior = db.scalars(
            select(ResumeVersion)
            .join(Job, Job.id == ResumeVersion.job_id)
            .where(
                ResumeVersion.user_id == job.user_id,
                Job.id != job.id,
                Job.company.ilike(f"%{cfg['reuse_order_from']}%"),
            )
            .order_by(ResumeVersion.created_at.desc())
        ).first()
        if prior:
            sec = next((x for x in prior.content.get("sections", []) if x.get("kind") == "projects"), None)
            order = [
                e.get("fields", {}).get("short_name") or e.get("fields", {}).get("primary", "")
                for e in (sec or {}).get("entries") or []
            ]
            cfg["project_priority"] = list(dict.fromkeys(cfg["project_priority"] + [o for o in order if o]))
        else:
            cfg["ambiguous"].append(
                f"No earlier resume for “{cfg['reuse_order_from']}” was found to copy the order from."
            )
    cfg["guidance"] = cfg["unrecognized"] + ([f"Notes: {job.notes.strip()}"] if (job.notes or "").strip() else [])
    cfg["summary_lines"] = summary_lines(cfg)
    return cfg


def resolve_template(db: Session, user_id: int, job: Job, template_id: int | None) -> ResumeTemplate:
    settings = db.get(UserSettings, user_id)
    override = ((job.resume_instructions or {}).get("controls") or {}).get("template_id")
    tpl_id = template_id or override or (settings.default_template_id if settings else None)
    tpl = db.get(ResumeTemplate, tpl_id) if tpl_id else None
    if tpl is None or tpl.user_id != user_id:
        raise AppError(
            "Upload your resume template first (Resumes > Templates).", code="no_template", actions=["open_templates"]
        )
    return tpl


def start_generation(db: Session, user_id: int, job_id: int, template_id: int | None) -> int:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user_id:
        raise NotFound("Job")
    if job.analysis_state != "ready":
        raise AppError("Wait for the job analysis to finish before generating a resume.", code="job_not_ready")
    tpl = resolve_template(db, user_id, job, template_id)
    task = enqueue(db, user_id, "resume_generate", {"job_id": job.id, "template_id": tpl.id}, GENERATE_STEPS)
    return task.id


def build_inputs(db: Session, job: Job, tpl: ResumeTemplate, cfg: dict | None = None) -> TailorInputs:
    user_id = job.user_id
    settings = db.get(UserSettings, user_id)
    cfg = cfg or {}
    weights: dict[str, float] = {}
    ordered: list[str] = []
    for r in job.requirements:
        if r.kind == "skill" and r.canonical:
            weights[r.canonical] = max(weights.get(r.canonical, 0), WEIGHT[r.importance])
            ordered.append(r.canonical)
    ordered.sort(key=lambda k: -weights[k])
    focus = JobFocus(weights, list(dict.fromkeys(ordered)), job.role_title, job.company)

    matches = {m.requirement_id: m for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
    strong = {
        r.canonical for r in job.requirements if r.canonical and matches.get(r.id) and matches[r.id].status == "strong"
    }

    recs = {rec.project_id: rec for rec in job.project_recommendations}
    chosen = [rec.project_id for rec in sorted(job.project_recommendations, key=lambda r: r.rank) if rec.selected]
    # projects the user named in their instructions are included even if the ranking did not select them
    priority = [_norm_name(n) for n in cfg.get("project_priority") or []]
    all_projects = db.scalars(
        select(Project)
        .where(Project.user_id == user_id)
        .options(selectinload(Project.evidence), selectinload(Project.bullets))
    ).all()
    for p in all_projects:
        if (
            p.evidence_eligible
            and p.id not in chosen
            and any(n and (n in _norm_name(p.title) or _norm_name(p.title) in n) for n in priority)
        ):
            chosen.append(p.id)
    by_id = {p.id: p for p in all_projects}
    projects = []
    for pid in chosen:
        p = by_id.get(pid)
        if p is None or not p.evidence_eligible:  # never a portfolio/profile asset or unfinished project
            continue
        rec = recs.get(pid)
        claimable = {e.canonical for e in p.evidence if e.canonical and e.status in ("verified", "user_provided")}
        tech_keys = [
            e.canonical
            for e in p.evidence
            if e.canonical
            and e.status in ("verified", "user_provided")
            and e.kind == "technology"
            and e.category not in ("concept", "security", "tool")
        ]
        tech_keys = list(dict.fromkeys(sorted(tech_keys, key=lambda k: -weights.get(k, 0))))
        features = [e.label for e in p.evidence if e.kind == "feature" and e.status in ("verified", "user_provided")]
        projects.append(
            ProjectInput(
                id=p.id,
                title=p.title,
                url=p.repo_url or p.homepage_url,
                description=p.description,
                bullets=[b.text for b in p.bullets],
                claimable=claimable,
                tech=[display_name(k) for k in tech_keys],
                features=features,
                matched=[m["label"] for m in rec.matched] if rec else [],
                score=rec.score if rec else 0,
            )
        )
    exps = [
        ExperienceInput(e.id, e.organization, e.title, [b.text for b in e.bullets])
        for e in db.scalars(
            select(Experience).where(Experience.user_id == user_id).options(selectinload(Experience.bullets))
        )
    ]
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    return TailorInputs(
        structure=_structure(tpl),
        focus=focus,
        projects=projects,
        experiences=exps,
        profile_skills=[s.canonical for s in db.scalars(select(Skill).where(Skill.user_id == user_id))],
        strong_skills=strong,
        profile_summary=profile.summary if profile else "",
        max_bullets_per_project=settings.max_bullets_per_project if settings else 3,
        config=cfg,
        guidance=cfg.get("guidance") or [],
    )


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _structure(tpl: ResumeTemplate) -> dict:
    """Re-parses LaTeX sources so rendering always uses the current parser's spans."""
    if tpl.format in ("latex", "pdf", "text") and tpl.source_text:
        try:
            return parse_latex_template(tpl.source_text)
        except TemplateParseError:
            return tpl.structure
    return tpl.structure


def _header_links(cfg: dict | None, profile: UserProfile | None) -> dict:
    if not cfg:
        return {}
    urls = {
        "portfolio": profile.portfolio_url if profile else "",
        "github": profile.github_url if profile else "",
        "linkedin": profile.linkedin_url if profile else "",
    }
    return {k: {"mode": v, "url": urls[k]} for k, v in cfg.get("links", {}).items() if v in ("include", "exclude")}


def _fit_block(level: int) -> str:
    spread = {1: "0.97", 2: "0.94"}[level]
    return f"{FIT_MARK}\n\\linespread{{{spread}}}\\selectfont\n{FIT_MARK}-end\n"


def render_content(
    tpl: ResumeTemplate, content: dict, cfg: dict | None = None, profile: UserProfile | None = None, fit_level: int = 0
) -> tuple[str | None, bytes | None, bytes | None, int | None, str]:
    """Returns (latex source, docx bytes, pdf, page count, error)."""
    instructions = to_render_instructions(content)
    if tpl.format in ("latex", "pdf", "text"):
        urls = {
            "portfolio": profile.portfolio_url if profile else "",
            "github": profile.github_url if profile else "",
            "linkedin": profile.linkedin_url if profile else "",
        }
        doc_links = (content.get("header") or {}).get("links")
        if doc_links:  # the document's own header state is the single source of truth
            links = {
                lk["kind"]: {"mode": lk["mode"], "url": lk.get("url") or urls.get(lk["kind"], "")}
                for lk in doc_links
                if lk.get("mode") in ("include", "exclude")
            }
        else:
            links = _header_links(cfg, profile)
        name = (content.get("header") or {}).get("name")
        try:
            src = render_latex(
                tpl.source_text or "",
                _structure(tpl),
                instructions,
                header_links=links,
                remove_sections=removed_sections(content),
                section_titles=section_titles(content),
                header_name=name,
            )
        except RenderError as exc:
            return None, None, None, None, str(exc)
        if fit_level:
            src = src.replace("\\begin{document}", _fit_block(fit_level) + "\\begin{document}", 1)
        res = compile_latex(src, tpl.structure.get("aux_files"))
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
        reqs = [
            {"id": r.id, "kind": r.kind, "label": r.label, "canonical": r.canonical, "importance": r.importance}
            for r in job.requirements
        ]
        matches = {m.requirement_id: m.status for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
    text = pdf_text(version.pdf) if version.pdf else ""
    version.ats_report = ats_check(
        text, version.content, reqs, matches, version.page_count, max_pages, tpl.structure.get("header", {})
    )
    version.recruiter_report = recruiter_check(version.content, reqs, matches, version.page_count, max_pages)


def seed_header(content: dict, header: dict, cfg: dict, profile: UserProfile | None) -> None:
    """Initial header state of the document: the template's links, plus the profile's links it could show, each
    with the mode chosen by the job instructions (or "template")."""
    kinds = {}
    for lk in header.get("links", []):
        k = "portfolio" if lk["kind"] == "website" else lk["kind"]
        if k in ("portfolio", "github", "linkedin"):
            kinds[k] = lk["url"]
    urls = {
        "portfolio": profile.portfolio_url if profile else "",
        "github": profile.github_url if profile else "",
        "linkedin": profile.linkedin_url if profile else "",
    }
    links = []
    for k in ("github", "linkedin", "portfolio"):
        url = kinds.get(k) or urls.get(k, "")
        if not url:
            continue
        mode = (cfg.get("links") or {}).get(k, "template")
        links.append({"id": f"header.link.{k}", "kind": k, "url": url, "mode": mode, "in_template": k in kinds})
    content["header"] = {"name": header.get("name", ""), "original_name": header.get("name", ""), "links": links}
    ensure_ids(content)


def drop_profile_assets(content: dict) -> list[str]:
    """Removes any portfolio/profile entry from the Projects section. Returns what was removed."""
    removed = []
    for sec in content.get("sections", []):
        if sec.get("kind") != "projects" or not sec.get("entries"):
            continue
        keep = []
        for e in sec["entries"]:
            name = e.get("fields", {}).get("primary", "")
            if is_personal_profile(
                e.get("fields", {}).get("short_name") or name, homepage=e.get("fields", {}).get("url", "")
            ) or PROFILE_ASSET_NAME.search(name):
                removed.append(name)
            else:
                keep.append(e)
        sec["entries"] = keep
        if not keep:
            sec["removed"] = True
    return removed


def run_gate(db: Session, v: ResumeVersion, job: Job | None, tpl: ResumeTemplate, cfg: dict) -> dict:
    user_id = v.user_id
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    projects = db.scalars(select(Project).where(Project.user_id == user_id)).all()
    eligible = {p.id: {"title": p.title, "eligible": p.evidence_eligible, "asset_type": p.asset_type} for p in projects}
    allowed = {s.name.lower() for s in db.scalars(select(Skill).where(Skill.user_id == user_id))}
    strong: set[str] = set()
    if job is not None:
        matches = {m.requirement_id: m.status for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
        strong = {r.canonical for r in job.requirements if r.canonical and matches.get(r.id) == "strong"}
    checks = evidence_checks(v.content, eligible, allowed, strong)
    header_urls = {
        "portfolio": profile.portfolio_url if profile else "",
        "github": profile.github_url if profile else "",
        "linkedin": profile.linkedin_url if profile else "",
    }
    check_cfg = dict(cfg)
    doc_links = (v.content.get("header") or {}).get("links")
    if doc_links:
        check_cfg["links"] = {lk["kind"]: lk["mode"] for lk in doc_links}
        header_urls.update({lk["kind"]: lk.get("url") or header_urls.get(lk["kind"], "") for lk in doc_links})
    if v.content.get("page_target"):
        check_cfg["length"] = v.content["page_target"]
    checks += instruction_checks(check_cfg, v.content, v.source_text, v.page_count, header_urls)
    claims = Check("user_claims", "Your own additions")
    typed = user_claims(v.content)
    if typed:
        claims.review(
            "You typed numbers or impact claims CareerPilot cannot verify. Keep them only if they are "
            "true and you can back them up.",
            typed,
        )
    checks.append(claims)

    tmpl = Check("template", "Template preserved")
    if v.source_text and tpl.source_text:
        gen = re.sub(re.escape(FIT_MARK) + r".*?" + re.escape(FIT_MARK) + r"-end\n", "", v.source_text, flags=re.S)
        if gen.split("\\begin{document}")[0] != tpl.source_text.split("\\begin{document}")[0]:
            tmpl.fail("The template's layout definitions were changed.")
        elif FIT_MARK in v.source_text:
            tmpl.detail = "Line spacing tightened slightly to fit the page limit; layout otherwise unchanged."
    elif not v.source_text:
        tmpl.detail = "Word template: content replaced inside your document's own paragraphs."
    checks.append(tmpl)

    if v.pdf:
        titles = [s["title"] for s in v.content.get("sections", []) if not s.get("removed")]
        checks += pdf_checks(v.pdf, _structure(tpl).get("header", {}), titles, profile.full_name if profile else "")
        checks.append(stuffing_check(pdf_text(v.pdf)))
    else:
        c = Check("pdf", "PDF rendered")
        c.fail(v.render_error or "No PDF was produced.")
        checks.append(c)
    sources = [p.readme_excerpt for p in projects if p.readme_excerpt] + [
        p.description for p in projects if p.description
    ]
    checks += language_checks(v.content, job.description_text if job else "", sources)
    report = summarize(checks)
    conflict = None
    if v.page_count and v.page_count > check_cfg.get("length", 99):
        conflict = {
            "pages": v.page_count,
            "target": check_cfg["length"],
            "options": length_options(v.content, check_cfg),
        }
    report["length_conflict"] = conflict
    report["config_summary"] = cfg.get("summary_lines", [])
    v.quality_report = report
    v.export_blocked = report["blocked"]
    return report


def length_options(content: dict, cfg: dict) -> list[dict]:
    opts = []
    proj = next((s for s in content["sections"] if s["kind"] == "projects" and not s.get("removed")), None)
    if proj and len(proj.get("entries") or []) > 1:
        opts.append({"id": "reduce_projects", "label": f"Use {len(proj['entries']) - 1} projects"})
    if any(len(e.get("bullets") or []) > 2 for s in content["sections"] for e in s.get("entries") or []):
        opts.append({"id": "shorten_bullets", "label": "Keep at most 2 bullets per entry"})
    if any(s["kind"] == "summary" and not s.get("removed") for s in content["sections"]):
        opts.append({"id": "remove_summary", "label": "Remove the summary"})
    if any(s["kind"] in ("certifications", "achievements") and not s.get("removed") for s in content["sections"]):
        opts.append({"id": "remove_certifications", "label": "Remove certifications"})
    opts.append({"id": "allow_more_pages", "label": f"Allow {cfg.get('length', 1) + 1} pages"})
    return opts


def fit_to_length(tpl, content, cfg, profile, changes) -> tuple:
    """Fits truthful content into the page limit in the documented order, recording every change."""
    src, docx, pdf, pages, err = render_content(tpl, content, cfg, profile)
    target = cfg.get("length", 1)
    steps = 0
    while pages and pages > target and steps < MAX_TRIM_STEPS:
        trial = copy.deepcopy(content)
        what = trim_for_length(trial, steps, keep_projects=cfg.get("projects") if cfg.get("projects_explicit") else 0)
        if what is None:
            break
        t = render_content(tpl, trial, cfg, profile)
        if t[2] is None:
            break
        content, (src, docx, pdf, pages, err) = trial, t
        changes.append({"section": "Length", "type": "trimmed", "detail": what})
        steps += 1
    fit_level = 0
    for level in (1, 2):
        if not (pages and pages > target and tpl.format in ("latex", "pdf", "text")):
            break
        t = render_content(tpl, content, cfg, profile, fit_level=level)
        if t[2] is not None and t[3] and t[3] < pages:
            (src, docx, pdf, pages, err), fit_level = t, level
            changes.append(
                {
                    "section": "Length",
                    "type": "trimmed",
                    "detail": "Tightened line spacing slightly to fit the page limit",
                }
            )
    return content, src, docx, pdf, pages, err, fit_level


@task_handler("resume_generate")
def generate_task(ctx: TaskContext) -> dict:
    db = ctx.db
    user_id = ctx.task.user_id
    job = db.get(Job, ctx.payload["job_id"])
    tpl = db.get(ResumeTemplate, ctx.payload["template_id"])
    if job is None or tpl is None or job.user_id != user_id or tpl.user_id != user_id:
        raise NotFound("Job or template")
    settings = db.get(UserSettings, user_id)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    provider = get_provider(settings)

    with ctx.step("Selecting evidence"):
        cfg = job_config(db, job, tpl, settings)
        inputs = build_inputs(db, job, tpl, cfg)
    with ctx.step("Applying your instructions"):
        if cfg["conflicts"]:
            ctx.note("Applying your instructions", f"{len(cfg['conflicts'])} not applied (conflict with evidence)")
    with ctx.step("Tailoring content"):
        content, changes = tailor(inputs, provider)
        if describe(provider):
            ctx.note("Tailoring content", describe(provider))
        seed_header(content, _structure(tpl).get("header", {}), cfg, profile)
        dropped = drop_profile_assets(content)
        for name in dropped:
            changes.append(
                {
                    "section": "Projects",
                    "type": "removed",
                    "detail": f"Removed {name}: a portfolio is a header link, never a project",
                }
            )
    with ctx.step("Rendering your template"):
        src, docx, pdf, pages, err = render_content(tpl, content, cfg, profile)
        if err and pdf is None and docx is None:
            raise AppError(
                err,
                code="render_failed",
                hint="Your template itself may not compile - check the template preview on the Templates page.",
            )
    with ctx.step("Fitting the page limit"):
        content, src, docx, pdf, pages, err, _ = fit_to_length(tpl, content, cfg, profile, changes)
        if pages and pages > cfg["length"]:
            ctx.note("Fitting the page limit", f"{pages} pages (target {cfg['length']}) - your decision needed")
    with ctx.step("Quality and evidence checks"):
        number = (
            db.scalar(
                select(func.max(ResumeVersion.version_number)).where(
                    ResumeVersion.user_id == user_id, ResumeVersion.job_id == job.id
                )
            )
            or 0
        ) + 1
        label = f"{job.company or 'Company'} — {job.role_title or 'Role'} — {datetime.now(UTC).strftime('%d %b %Y')}"
        version = ResumeVersion(
            user_id=user_id,
            job_id=job.id,
            template_id=tpl.id,
            version_number=number,
            label=label,
            content=content,
            source_text=src,
            source_bytes=docx,
            pdf=pdf,
            page_count=pages,
            render_error=err,
            changes=changes,
            used_llm=bool(content.get("used_llm")),
            config=cfg,
        )
        db.add(version)
        db.flush()
        report = run_gate(db, version, job, tpl, cfg)
        if provider is not None:
            flagged = [
                i.split(" in: ", 1)[-1]
                for c in report["checks"]
                if c["id"] in ("generic_language",)
                for i in c["items"]
            ]
            flagged += [i for c in report["checks"] if c["id"] in ("first_person", "tense") for i in c["items"]]
            full = [
                b["text"]
                for s in content["sections"]
                for e in s.get("entries") or []
                for b in e.get("bullets") or []
                if any(b["text"].startswith(f[:60]) for f in flagged)
            ]
            improved = polish(content, provider, full, changes) if full else 0
            if improved:
                content, src, docx, pdf, pages, err, _ = fit_to_length(tpl, content, cfg, profile, changes)
                version.content, version.source_text, version.source_bytes = content, src, docx
                version.pdf, version.page_count, version.render_error = pdf, pages, err
                changes.append(
                    {
                        "section": "All",
                        "type": "rewritten",
                        "detail": f"Rewrote {improved} generic or inconsistent bullet(s)",
                    }
                )
                version.changes = changes
                report = run_gate(db, version, job, tpl, cfg)
            report["generic_rewritten"] = improved
        review(db, job, tpl, version, cfg["length"])
        app_row = db.scalar(select(Application).where(Application.job_id == job.id, Application.user_id == user_id))
        if app_row:
            if app_row.status in ("saved", "analyzing"):
                app_row.status = "resume_ready"
                db.add(
                    ApplicationEvent(
                        application_id=app_row.id,
                        kind="status",
                        message="Resume generated",
                        data={"to": "resume_ready", "resume_version_id": version.id},
                    )
                )
            if app_row.resume_version_id is None or app_row.status in ("saved", "resume_ready"):
                app_row.resume_version_id = version.id
        db.commit()
        if report["blocked"]:
            failing = [c["label"] for c in report["checks"] if c["status"] == "fail"]
            ctx.note("Quality and evidence checks", "Export blocked: " + "; ".join(failing))
    return {"resume_version_id": version.id, "blocked": report["blocked"]}


LENGTH_ACTIONS = ("reduce_projects", "shorten_bullets", "remove_summary", "remove_certifications", "allow_more_pages")


def resolve_length(db: Session, v: ResumeVersion, option: str) -> ResumeVersion:
    if v.status == "approved" or v.is_base:
        raise AppError("Approved versions can't be changed. Duplicate it first.", code="version_locked")
    if option not in LENGTH_ACTIONS:
        raise AppError("Unknown option.", code="invalid_option")
    content = copy.deepcopy(v.content)
    cfg = dict(v.config or {})
    note = ""
    if option == "reduce_projects":
        proj = next((s for s in content["sections"] if s["kind"] == "projects" and s.get("entries")), None)
        if proj and len(proj["entries"]) > 1:
            gone = proj["entries"].pop()
            note = f"Removed project {gone['fields'].get('primary', '')} (your choice)"
            cfg["projects"] = len(proj["entries"])
    elif option == "shorten_bullets":
        for s in content["sections"]:
            for e in s.get("entries") or []:
                if e.get("bullets") and len(e["bullets"]) > 2:
                    e["bullets"] = e["bullets"][:2]
        note = "Kept at most 2 bullets per entry (your choice)"
    elif option in ("remove_summary", "remove_certifications"):
        kind = option.split("_", 1)[1]
        for s in content["sections"]:
            if s["kind"] == kind or (kind == "certifications" and s["kind"] == "achievements" and s.get("items")):
                if s["kind"] == "achievements":
                    s["items"] = [t for t in s["items"] if not CERT_ITEM.search(t)]
                    if not s["items"]:
                        s["removed"] = True
                else:
                    s["removed"] = True
        cfg[kind] = "exclude"
        note = f"Removed {kind} (your choice)"
    elif option == "allow_more_pages":
        cfg["length"] = int(cfg.get("length", 1)) + 1
        note = f"Allowed {cfg['length']} pages (your choice)"
    v.content, v.config = content, cfg
    v.changes = list(v.changes or []) + [{"section": "Length", "type": "decision", "detail": note}]
    rerender_version(db, v)
    db.commit()
    return v


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
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == v.user_id))
    cfg = v.config or {}
    drop_profile_assets(v.content)
    src, docx, pdf, pages, err = render_content(tpl, v.content, cfg or None, profile)
    v.source_text, v.source_bytes, v.page_count, v.render_error = src, docx, pages, err
    if pdf is not None:
        v.pdf = pdf
    job = db.get(Job, v.job_id) if v.job_id else None
    settings = db.get(UserSettings, v.user_id)
    run_gate(db, v, job, tpl, cfg or {"length": settings.resume_max_pages if settings else 1, "links": {}})
    review(db, job, tpl, v, (cfg or {}).get("length") or (settings.resume_max_pages if settings else 1))


def duplicate_version(db: Session, v: ResumeVersion) -> ResumeVersion:
    number = (
        db.scalar(
            select(func.max(ResumeVersion.version_number)).where(
                ResumeVersion.user_id == v.user_id, ResumeVersion.job_id == v.job_id
            )
        )
        or 0
    ) + 1
    dup = ResumeVersion(
        user_id=v.user_id,
        job_id=v.job_id,
        template_id=v.template_id,
        parent_id=v.id,
        version_number=number,
        label=v.label.replace("Original — ", "Copy of "),
        status="draft",
        content=copy.deepcopy(v.content),
        source_text=v.source_text,
        source_bytes=v.source_bytes,
        pdf=v.pdf,
        page_count=v.page_count,
        ats_report=v.ats_report,
        recruiter_report=v.recruiter_report,
        changes=[{"section": "All", "type": "duplicated", "detail": f"Copied from version {v.id}"}],
        used_llm=v.used_llm,
    )
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
