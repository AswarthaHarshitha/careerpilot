"""Company research and interview preparation for a job.

Research reads only public pages the user's job points to (the posting and the company's own site), through the
same SSRF-safe fetcher as job import. Every stated fact carries the URL it came from; anything the agent
concludes is marked as inference. Nothing is presented as known that no fetched page says.

Interview preparation is built from the job's requirements, the resume version for the job, verified project
evidence and the profile. Every question is labelled as likely/suggested preparation - never as a known
interview question.
"""

import logging
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.fetch import ExternalFetchError, fetch
from app.jobs.html import html_to_text, page_meta
from app.llm.provider import LLMError, get_provider, wrap_untrusted
from app.llm.router import describe
from app.matching.taxonomy import display_name, find_skills
from app.models import Application, Job, JobMatch, Project, ResumeVersion, UserProfile, UserSettings
from app.tasks.queue import TaskContext, enqueue, task_handler

log = logging.getLogger(__name__)

ATS_HOSTS = re.compile(
    r"greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com|workday\.com|taleo\.net|"
    r"oraclecloud\.com|successfactors|icims\.com|linkedin\.com|indeed\.|naukri\.com|glassdoor\.|wellfound\.com"
)
PAGES = ("", "about", "about-us", "company", "engineering", "blog", "careers")
MAX_PAGES = 6


def company_site(job: Job) -> str:
    """The company's own website if the job links to it (not an applicant-tracking system)."""
    if job.company_website:
        return job.company_website
    for url in (job.job_url, job.application_url):
        parts = urlsplit(url or "")
        host = parts.hostname or ""
        if not host or ATS_HOSTS.search(host):
            continue
        if re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
            return f"{parts.scheme}://{parts.netloc}"
        labels = host.split(".")
        keep = 3 if len(labels) > 2 and labels[-2] in ("co", "com", "org", "ac") else 2
        return f"https://{'.'.join(labels[-keep:])}"
    return ""


def _read(url: str) -> dict | None:
    try:
        r = fetch(url, max_bytes=2_000_000)
    except ExternalFetchError:
        return None
    if "html" not in r.content_type:
        return None
    soup = BeautifulSoup(r.text, "lxml")
    meta = page_meta(soup)
    desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    for t in soup(["script", "style", "noscript", "nav", "footer", "form"]):
        t.decompose()
    text = html_to_text(str(soup.body or soup))[:12000]
    links = [urljoin(r.url, a.get("href", "")) for a in soup.find_all("a", href=True)][:300]
    return {
        "url": r.url,
        "title": meta["title"] or meta["og_title"],
        "description": (desc.get("content") or "").strip() if desc else "",
        "text": text,
        "links": links,
    }


def gather(job: Job, site: str) -> list[dict]:
    pages: list[dict] = []
    seen: set[str] = set()
    for path in PAGES:
        if len(pages) >= MAX_PAGES or not site:
            break
        url = site.rstrip("/") + ("/" + path if path else "/")
        page = _read(url)
        if page and page["url"] not in seen and len(page["text"]) > 120:
            seen.add(page["url"])
            pages.append(page)
    # the engineering blog, if the homepage links to one on the same site
    if pages and len(pages) < MAX_PAGES:
        host = urlsplit(site).netloc
        blog = next(
            (
                u
                for u in pages[0]["links"]
                if urlsplit(u).netloc.endswith(host)
                and re.search(r"engineering|tech(nology)?-?blog|/blog", u)
                and u not in seen
            ),
            None,
        )
        if blog:
            page = _read(blog)
            if page and len(page["text"]) > 120:
                pages.append(page)
    return pages


RESEARCH_SYSTEM = (
    "Summarise a company for a candidate preparing for an interview, using ONLY the pages given. Each page starts "
    "with its URL. Facts must be stated by a page and cite that page's URL exactly. Anything you conclude "
    "yourself goes in the inference lists and must not be phrased as fact. If the pages say little, say little. "
    'Return JSON: {"overview": [{"statement": "...", "source": "<url>"}], '
    '"recent_work": [{"statement": "...", "source": "<url>"}], '
    '"engineering_areas": [{"statement": "...", "source": "<url>"}], '
    '"role_context": ["inference about how this role fits, from the job description"], '
    '"interview_themes": ["inference"], "preparation": ["concrete preparation step"]}'
)


def research(db: Session, job: Job, provider, pages: list[dict] | None = None) -> dict:
    site = company_site(job)
    if pages is None:
        pages = gather(job, site) if site else []
    urls = {p["url"] for p in pages}
    tech: dict[str, str] = {}
    for p in pages:
        for m in find_skills(p["text"], include_weak=False):
            tech.setdefault(display_name(m.key), p["url"])
    jd_skills = [r.label for r in job.requirements if r.kind == "skill"][:12]
    out = {
        "website": site,
        "sources": [{"url": p["url"], "title": p["title"]} for p in pages],
        "overview": [{"statement": p["description"], "source": p["url"]} for p in pages[:2] if p["description"]],
        "technology_signals": [{"statement": f"{k} is mentioned", "source": u} for k, u in list(tech.items())[:15]],
        "engineering_areas": [],
        "recent_work": [],
        "role_context": [f"The posting asks for {', '.join(jd_skills[:6])}."] if jd_skills else [],
        "interview_themes": [f"Hands-on questions on {s}" for s in jd_skills[:4]],
        "preparation": [],
        "limitations": [],
        "generated_by": "",
    }
    if not site:
        out["limitations"].append(
            "The job links only to an applicant-tracking site, so the company's own website is unknown. Add it on "
            "this page to research it."
        )
    elif not pages:
        out["limitations"].append(f"{site} could not be read (it may block automated readers or need JavaScript).")
    if provider is not None and (pages or job.description_text):
        body = "\n\n".join(f"URL: {p['url']}\nTITLE: {p['title']}\n{p['text'][:5000]}" for p in pages)
        prompt = (
            f"Company: {job.company}\nRole: {job.role_title}\n\n"
            + wrap_untrusted("COMPANY PAGES", body or "(none)", 26000)
            + "\n\n"
            + wrap_untrusted("JOB DESCRIPTION", job.description_text[:6000], 6000)
        )
        try:
            data = provider.complete_json(RESEARCH_SYSTEM, prompt, max_tokens=2500, temperature=0.2)
        except LLMError as exc:
            out["limitations"].append(f"AI summary unavailable ({exc}); showing what the pages state directly.")
        else:
            for key in ("overview", "recent_work", "engineering_areas"):
                cited = [
                    {"statement": str(x.get("statement", ""))[:400], "source": x.get("source")}
                    for x in data.get(key) or []
                    if isinstance(x, dict) and x.get("source") in urls and x.get("statement")
                ]
                if cited or key != "overview":
                    out[key] = cited or out[key]
            for key in ("role_context", "interview_themes", "preparation"):
                vals = [str(x)[:300] for x in data.get(key) or [] if isinstance(x, str)]
                if vals:
                    out[key] = vals[:8]
            out["generated_by"] = describe(provider)
    if not out["preparation"]:
        out["preparation"] = [
            f"Be ready to explain where you used {s}, with a concrete example." for s in jd_skills[:3]
        ]
    return out


def _resume_for(db: Session, job: Job) -> ResumeVersion | None:
    a = db.scalar(select(Application).where(Application.job_id == job.id))
    if a and a.resume_version_id:
        return db.get(ResumeVersion, a.resume_version_id)
    return db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.job_id == job.id, ResumeVersion.is_base.is_(False))
        .order_by(ResumeVersion.created_at.desc())
    )


EVIDENCE_NOTE = {"strong": "strong evidence", "missing": "gap: prepare honestly", "partial": "partial evidence"}

BEHAVIORAL = [
    "Tell me about a time you had to learn a new technology quickly to finish a task.",
    "Describe a bug that was hard to find. How did you narrow it down?",
    "Tell me about a disagreement on a technical decision and how it was resolved.",
    "Describe a time you had to cut scope to meet a deadline.",
]
CODING = {
    "dsa": [
        "arrays and hashing",
        "two pointers / sliding window",
        "trees and graphs (BFS/DFS)",
        "time and space complexity",
    ],
    "sql": ["joins and aggregation", "window functions", "indexes and query plans"],
    "system_design": ["designing a REST API", "caching and consistency", "queues and background work"],
    "python": ["data structures in Python", "generators and iterators", "error handling and testing"],
}

PREP_SYSTEM = (
    "Prepare a candidate for interviews using ONLY the facts given (their resume items, verified project "
    "evidence, profile and the job's requirements). Questions are likely/suggested, never presented as known. "
    "Answer outlines use only the given facts; where a fact is missing, write what the candidate should check. "
    'Return JSON: {"technical_areas": ["..."], "project_questions": [{"project": "...", "question": "...", '
    '"answer_outline": "..."}], "resume_questions": [{"item": "...", "question": "...", "answer_outline": "..."}], '
    '"role_questions": ["..."], "behavioral": ["..."], "coding_topics": ["..."]}'
)


def interview_prep(db: Session, job: Job, provider) -> dict:
    user_id = job.user_id
    reqs = sorted(job.requirements, key=lambda r: {"required": 0, "preferred": 1}.get(r.importance, 2))
    matches = {m.requirement_id: m.status for m in db.scalars(select(JobMatch).where(JobMatch.job_id == job.id))}
    skills = [r for r in reqs if r.kind == "skill"]
    v = _resume_for(db, job)
    content = v.content if v else {"sections": []}
    projects_on_resume = [
        e
        for s in content["sections"]
        if s["kind"] == "projects" and not s.get("removed")
        for e in s.get("entries") or []
    ]
    all_projects = {
        re.sub(r"[^a-z0-9]", "", (p.title or "").lower()): p
        for p in db.scalars(select(Project).where(Project.user_id == user_id))
    }
    out = {
        "resume_version": v.version_number if v else None,
        "label": "Likely / suggested preparation — generated from your resume, evidence and the job description. "
        "These are not known interview questions.",
        "technical_areas": [
            f"{r.label} ({EVIDENCE_NOTE.get(matches.get(r.id, ''), 'partial evidence')})" for r in skills[:10]
        ],
        "project_questions": [],
        "resume_questions": [],
        "role_questions": [f"How would you approach: {x}" for x in (job.responsibilities or [])[:5]],
        "behavioral": BEHAVIORAL,
        "coding_topics": [],
        "generated_by": "",
    }
    for e in projects_on_resume[:4]:
        name = e["fields"].get("short_name") or e["fields"].get("primary", "")
        p = next((pr for k, pr in all_projects.items() if k and (k in re.sub(r"[^a-z0-9]", "", name.lower()))), None)
        tech = [ev.label for ev in (p.evidence if p else []) if ev.status == "verified" and ev.kind == "technology"][:5]
        out["project_questions"] += [
            {
                "project": name,
                "question": f"Walk me through the architecture of {name}. What were the main components?",
                "answer_outline": "Start from the problem, then components"
                + (f" ({', '.join(tech)})" if tech else "")
                + ", data flow, and one trade-off you made.",
            },
            {
                "project": name,
                "question": f"What was the hardest problem in {name}, and how did you solve it?",
                "answer_outline": "Pick one concrete issue from the repository history; describe cause, fix "
                "and how you verified it.",
            },
        ]
    for s in content["sections"]:
        if s["kind"] != "experience" or s.get("removed"):
            continue
        for e in s.get("entries") or []:
            for b in [b for b in e.get("bullets") or [] if not b.get("tech_line")][:2]:
                out["resume_questions"].append(
                    {
                        "item": b["text"],
                        "question": "Tell me more about this. What exactly was your part?",
                        "answer_outline": "Situation, your specific contribution, tools used, and the result "
                        "you can verify.",
                    }
                )
    keys = {r.canonical for r in skills}
    for k, topics in CODING.items():
        if k in keys or (k == "dsa" and keys & {"algorithms", "data_structures"}):
            out["coding_topics"] += topics
    out["coding_topics"] = out["coding_topics"][:10] or CODING["dsa"]

    if provider is not None:
        profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
        facts = {
            "requirements": [f"{r.label} ({r.importance}, evidence: {matches.get(r.id, 'none')})" for r in skills[:15]],
            "resume_items": [
                b["text"]
                for s in content["sections"]
                for e in s.get("entries") or []
                for b in e.get("bullets") or []
                if not b.get("tech_line")
            ][:25],
            "projects": [
                {"name": e["fields"].get("primary"), "bullets": [b["text"] for b in e.get("bullets") or []]}
                for e in projects_on_resume[:4]
            ],
            "summary": profile.summary if profile else "",
            "company_research": (job.research or {}).get("overview", [])[:4],
        }
        import json

        prompt = f"Company: {job.company}\nRole: {job.role_title}\n\n" + wrap_untrusted(
            "CANDIDATE FACTS AND JOB", json.dumps(facts, ensure_ascii=False), 16000
        )
        try:
            data = provider.complete_json(PREP_SYSTEM, prompt, max_tokens=3500, temperature=0.3)
        except LLMError:
            pass
        else:
            for key in ("technical_areas", "role_questions", "behavioral", "coding_topics"):
                vals = [str(x)[:300] for x in data.get(key) or [] if isinstance(x, str)]
                if vals:
                    out[key] = vals[:10]
            for key, fields in (
                ("project_questions", ("project", "question", "answer_outline")),
                ("resume_questions", ("item", "question", "answer_outline")),
            ):
                vals = [
                    {f: str(x.get(f, ""))[:500] for f in fields}
                    for x in data.get(key) or []
                    if isinstance(x, dict) and x.get("question")
                ]
                if vals:
                    out[key] = vals[:10]
            out["generated_by"] = describe(provider)
    return out


# --- tasks --------------------------------------------------------------------------------------------


def _owned_job(db: Session, user_id: int, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user_id:
        raise AppError("Job not found.", code="not_found", status_code=404)
    return job


def start_research(db: Session, user_id: int, job_id: int, website: str = "") -> int:
    job = _owned_job(db, user_id, job_id)
    if website:
        from app.core.fetch import validate_url

        job.company_website = validate_url(website)[:300]
        db.commit()
    return enqueue(
        db, user_id, "company_research", {"job_id": job.id}, ["Reading public company pages", "Summarising"]
    ).id


def start_prep(db: Session, user_id: int, job_id: int) -> int:
    job = _owned_job(db, user_id, job_id)
    return enqueue(db, user_id, "interview_prep", {"job_id": job.id}, ["Preparing questions"]).id


@task_handler("company_research")
def research_task(ctx: TaskContext) -> dict:
    job = _owned_job(ctx.db, ctx.task.user_id, ctx.payload["job_id"])
    provider = get_provider(ctx.db.get(UserSettings, job.user_id))
    with ctx.step("Reading public company pages"):
        site = company_site(job)
        pages = gather(job, site) if site else []
        ctx.note(
            "Reading public company pages", f"{len(pages)} page(s) from {site}" if site else "No company website known"
        )
    with ctx.step("Summarising"):
        job.research = research(ctx.db, job, provider, pages)
        job.research_at = datetime.now(UTC)
        ctx.db.commit()
    return {"sources": len(job.research["sources"])}


@task_handler("interview_prep")
def prep_task(ctx: TaskContext) -> dict:
    job = _owned_job(ctx.db, ctx.task.user_id, ctx.payload["job_id"])
    provider = get_provider(ctx.db.get(UserSettings, job.user_id))
    with ctx.step("Preparing questions"):
        job.interview_prep = interview_prep(ctx.db, job, provider)
        job.interview_prep_at = datetime.now(UTC)
        ctx.db.commit()
    return {"questions": len(job.interview_prep["project_questions"]) + len(job.interview_prep["resume_questions"])}
