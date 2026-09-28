"""Builds a job-specific resume document from the career profile, project evidence and the template.

The result is a structured document (stored with the resume version) plus a list of changes, each with its
reason. Content comes only from the profile, the user's own resume bullets and verified project evidence. A
configured language model may reword bullets and the summary; every rewrite passes the truth checks in
app.resume.truth or is discarded.
"""

import logging
import re
from dataclasses import dataclass, field

from app.llm.provider import LLMError, LLMProvider, wrap_untrusted
from app.matching.taxonomy import SKILLS, category_of, display_name, find_skills
from app.resume.truth import allowed_skills_for, check_rewrite

log = logging.getLogger(__name__)
WEIGHT = {"required": 3.0, "preferred": 1.5, "keyword": 1.0}


@dataclass
class JobFocus:
    """What the job cares about: canonical skill -> weight, and display names in the posting's order."""

    weights: dict[str, float]
    ordered: list[str]
    role: str = ""
    company: str = ""


@dataclass
class ProjectInput:
    id: int
    title: str
    url: str
    description: str
    bullets: list[str]
    claimable: set[str]  # canonical skills with verified/user-provided evidence
    tech: list[str]  # display names of verified technologies, most relevant first
    features: list[str]  # verified README feature statements
    matched: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class ExperienceInput:
    id: int
    organization: str
    title: str
    bullets: list[str]


@dataclass
class TailorInputs:
    structure: dict
    focus: JobFocus
    projects: list[ProjectInput]  # selected projects, best first
    experiences: list[ExperienceInput]
    profile_skills: list[str]  # canonical keys the user listed
    strong_skills: set[str]  # JD skills with claimable evidence (from matching)
    profile_summary: str
    max_bullets_per_project: int = 3


def relevance(text: str, focus: JobFocus) -> float:
    keys = {m.key for m in find_skills(text)}
    return sum(focus.weights.get(k, 0.0) for k in keys)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _match_entry(entries: list[dict], name: str, url: str = "") -> int | None:
    n = _norm(name)
    for i, e in enumerate(entries):
        f = e.get("fields", {})
        if url and f.get("url") and _norm(f["url"]) == _norm(url):
            return i
    for i, e in enumerate(entries):
        f = e.get("fields", {})
        for cand in (f.get("primary", ""), f.get("short_name", "")):
            c = _norm(cand)
            if c and n and (c == n or (len(c) >= 4 and (c in n or n in c))):
                return i
    return None


def evidence_bullets(p: ProjectInput, focus: JobFocus) -> list[str]:
    """Bullets for a project that has no user-written bullets, assembled only from verified facts:
    the repository description the user wrote and its verified technologies/features."""
    out = []
    tech = [t for t in p.tech][:5]
    desc = p.description.strip().rstrip(".")
    if desc:
        stem = desc[0].upper() + desc[1:]
        if tech:
            out.append(f"{stem}; built with {_join(tech)}.")
        else:
            out.append(stem + ".")
    elif tech:
        out.append(f"Built {p.title} with {_join(tech)}.")
    feats = sorted(p.features, key=lambda f: -relevance(f, focus))
    for f in feats[:2]:
        f = f.rstrip(".")
        out.append(f[0].upper() + f[1:] + ".")
    return out


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# Template skill category names -> taxonomy categories they accept.
CATEGORY_HINTS = [
    (re.compile(r"language", re.I), {"language"}),
    (re.compile(r"framework|librar|backend|web|frontend|full", re.I), {"backend", "frontend", "mobile"}),
    (re.compile(r"database|data ?store|storage", re.I), {"database"}),
    (re.compile(r"cloud|devops|infra|deploy", re.I), {"cloud", "devops"}),
    (re.compile(r"ai|ml|machine|data|analytics", re.I), {"ml", "data"}),
    (re.compile(r"tool|platform|system", re.I), {"tool", "devops", "cloud", "testing"}),
    (re.compile(r"test|quality", re.I), {"testing"}),
    (re.compile(r"engineering|concept|core|fundamental|software|cs", re.I), {"concept", "security", "testing"}),
]


def tailor(inputs: TailorInputs, provider: LLMProvider | None = None) -> tuple[dict, list[dict]]:
    focus = inputs.focus
    changes: list[dict] = []
    sections_out = []
    rewrite_jobs: list[dict] = []  # bullets and summary eligible for model rewording

    for sec in inputs.structure["sections"]:
        kind, layout = sec["kind"], sec["layout"]
        base = {"index": sec["index"], "title": sec["title"], "kind": kind, "layout": layout}

        if kind == "summary" and layout == "paragraph":
            original = sec["paragraph"]["text"]
            base.update({"text": original, "original_text": original})
            rewrite_jobs.append({"type": "summary", "section": base, "source": original + "\n" + inputs.profile_summary})
            sections_out.append(base)

        elif kind == "experience" and layout == "entries":
            entries = []
            for i, e in enumerate(sec["entries"]):
                exp = _find_experience(inputs.experiences, e["fields"])
                bullets = exp.bullets if exp and exp.bullets else [it["text"] for it in e["items"]]
                if not bullets:
                    entries.append({"source_index": i, "fields": e["fields"], "bullets": None,
                                    "ref": {"type": "experience", "id": exp.id} if exp else None})
                    continue
                ordered = sorted(bullets, key=lambda b: -relevance(b, focus))
                entry = {"source_index": i, "fields": e["fields"],
                         "ref": {"type": "experience", "id": exp.id} if exp else None,
                         "bullets": [{"text": b, "original": b, "origin": "profile"} for b in ordered]}
                if ordered != bullets:
                    changes.append({"section": sec["title"], "type": "reordered",
                                    "detail": f"Put the most relevant bullets first for {e['fields'].get('primary', '')}"})
                allowed = allowed_skills_for(bullets, set())
                for b in entry["bullets"]:
                    rewrite_jobs.append({"type": "bullet", "bullet": b, "source": "\n".join(bullets),
                                         "allowed": allowed, "context": e["fields"].get("primary", "")})
                entries.append(entry)
            base["entries"] = entries
            sections_out.append(base)

        elif kind == "projects" and layout == "entries":
            entries = []
            used_template = set()
            for p in inputs.projects:
                idx = _match_entry(sec["entries"], p.title, p.url)
                if idx is not None and idx in used_template:
                    idx = None
                if idx is not None:
                    used_template.add(idx)
                if p.bullets:
                    pool = [(b, "profile") for b in p.bullets]
                elif idx is not None and sec["entries"][idx]["items"]:
                    pool = [(it["text"], "template") for it in sec["entries"][idx]["items"]]
                else:
                    pool = [(b, "evidence") for b in evidence_bullets(p, focus)]
                if not pool:
                    continue
                ranked = sorted(pool, key=lambda bo: -relevance(bo[0], focus))
                keep = ranked[: max(1, inputs.max_bullets_per_project)]
                # keep the user's original order among the chosen bullets for readability
                keep.sort(key=lambda bo: pool.index(bo))
                fields = dict(sec["entries"][idx]["fields"]) if idx is not None else {
                    "primary": p.title, "url": p.url, "tech": ", ".join(p.tech[:5]),
                }
                entry = {"source_index": idx, "fields": fields, "ref": {"type": "project", "id": p.id},
                         "matched": p.matched[:8],
                         "bullets": [{"text": b, "original": b, "origin": o} for b, o in keep]}
                allowed = p.claimable | allowed_skills_for([b for b, _ in pool], set())
                source = "\n".join([p.description] + [b for b, _ in pool] + p.features)
                for b in entry["bullets"]:
                    rewrite_jobs.append({"type": "bullet", "bullet": b, "source": source, "allowed": allowed,
                                         "context": p.title})
                entries.append(entry)
                why = ", ".join(p.matched[:5]) or "related evidence"
                changes.append({"section": sec["title"], "type": "selected",
                                "detail": f"Included {p.title} - evidence for {why}"})
                if any(o == "evidence" for _, o in keep):
                    changes.append({"section": sec["title"], "type": "drafted",
                                    "detail": f"{p.title}: bullets drafted from the repository description and "
                                              "verified evidence - review the wording"})
            dropped = [e["fields"].get("primary", "") for i, e in enumerate(sec["entries"]) if i not in used_template]
            for d in dropped:
                if d:
                    changes.append({"section": sec["title"], "type": "removed",
                                    "detail": f"Left out {d} (less relevant to this job)"})
            if not entries:  # nothing selected - keep the template's projects as they are
                entries = [{"source_index": i, "fields": e["fields"], "bullets": None}
                           for i, e in enumerate(sec["entries"])]
            base["entries"] = entries
            sections_out.append(base)

        elif layout == "skills":
            cats, skill_changes = _tailor_skills(sec["skills"], inputs, focus)
            base["categories"] = cats
            changes += [{"section": sec["title"], **c} for c in skill_changes]
            sections_out.append(base)

        elif layout == "items":
            items = [it["text"] for it in sec["items"]]
            ordered = sorted(items, key=lambda t: -relevance(t, focus))
            base["items"] = ordered
            if ordered != items:
                changes.append({"section": sec["title"], "type": "reordered", "detail": "Most relevant items first"})
            sections_out.append(base)
        else:
            base["unchanged"] = True
            sections_out.append(base)

    used_llm = False
    if provider is not None and rewrite_jobs:
        used_llm = _llm_rewrite(provider, rewrite_jobs, focus, changes)

    content = {"sections": sections_out, "focus": focus.ordered[:15], "used_llm": used_llm}
    return content, changes


def _find_experience(exps: list[ExperienceInput], fields: dict) -> ExperienceInput | None:
    org = _norm(fields.get("primary", ""))
    for e in exps:
        o = _norm(e.organization)
        if o and org and (o == org or o in org or org in o):
            return e
    return None


def _tailor_skills(categories: list[dict], inputs: TailorInputs, focus: JobFocus) -> tuple[list[dict], list[dict]]:
    changes = []
    out = []
    listed_keys = set()
    for c in categories:
        for item in c["items"]:
            for m in find_skills(item):
                listed_keys.add(m.key)
    # JD skills the user can back up with evidence but the skills section does not mention
    additions = [k for k in focus.ordered if k in inputs.strong_skills and k not in listed_keys and k in SKILLS]
    placed: set[str] = set()
    cat_targets = []
    for c in categories:
        accepts = set()
        for rx, cats in CATEGORY_HINTS:
            if rx.search(c["name"]):
                accepts |= cats
        cat_targets.append(accepts)
    for c, accepts in zip(categories, cat_targets, strict=True):
        items = list(c["items"])

        def score(item: str) -> float:
            return relevance(item, focus)

        ordered = sorted(items, key=lambda it: -score(it))
        added = []
        for k in additions:
            if k in placed or category_of(k) not in accepts:
                continue
            added.append(display_name(k))
            placed.add(k)
        new_items = ordered + added
        if ordered != items:
            moved = [it for it in ordered[:3] if score(it) > 0]
            if moved:
                changes.append({"type": "reordered", "detail": f"{c['name']}: moved {', '.join(moved)} to the front"})
        if added:
            changes.append({"type": "added", "detail": f"{c['name']}: added {', '.join(added)} "
                                                       "(demonstrated in your projects)"})
        out.append({"name": c["name"], "items": new_items, "original": items})
    return out, changes


LLM_SYSTEM = (
    "You tailor resume wording for one job. For each item, rewrite it to emphasise what matters for the job, "
    "keeping every fact identical. Rules: do not add technologies, numbers, users, customers, scale, impact, "
    "leadership or awards that the original does not state; keep each bullet under 30 words; start bullets with a "
    "strong past-tense verb; no first person. If an item cannot be improved, return it unchanged. "
    'Return JSON: {"items": {"<id>": "<rewritten text>"}}'
)


def _llm_rewrite(provider: LLMProvider, jobs: list[dict], focus: JobFocus, changes: list[dict]) -> bool:
    items = {}
    for n, job in enumerate(jobs):
        key = f"i{n}"
        job["key"] = key
        if job["type"] == "summary":
            items[key] = {"kind": "summary", "text": job["section"]["text"]}
        else:
            items[key] = {"kind": "bullet", "entry": job["context"], "text": job["bullet"]["text"]}
    focus_names = ", ".join(display_name(k) for k in focus.ordered[:15])
    prompt = (
        f"Target role: {focus.role or 'not stated'} at {focus.company or 'the company'}.\n"
        f"Skills the job emphasises (in order): {focus_names}.\n\n"
        "Items to rewrite (JSON):\n" + wrap_untrusted("RESUME ITEMS", _json(items), 20_000)
    )
    try:
        data = provider.complete_json(LLM_SYSTEM, prompt, max_tokens=4000, temperature=0.3)
    except LLMError as exc:
        changes.append({"section": "All", "type": "note", "detail": f"Model rewording unavailable ({exc}); kept your "
                                                                    "original wording."})
        return False
    rewritten = data.get("items") or {}
    accepted = rejected = 0
    for job in jobs:
        new = rewritten.get(job["key"])
        if not isinstance(new, str) or not new.strip():
            continue
        new = re.sub(r"\s+", " ", new).strip()
        if job["type"] == "summary":
            sec = job["section"]
            if _norm(new) == _norm(sec["text"]):
                continue
            verdict = check_rewrite(new, job["source"], allowed_skills_for([job["source"]], set()))
            if verdict.ok:
                sec["text"] = new
                accepted += 1
                changes.append({"section": sec["title"], "type": "rewritten", "detail": "Summary aligned with the role"})
            else:
                rejected += 1
                changes.append({"section": sec["title"], "type": "kept", "detail": "Kept your summary: the suggested "
                                "rewrite " + "; ".join(verdict.problems[:2])})
        else:
            b = job["bullet"]
            if _norm(new) == _norm(b["text"]):
                continue
            verdict = check_rewrite(new, job["source"], job["allowed"])
            if verdict.ok:
                b["text"] = new
                b["origin"] = "rewritten"
                accepted += 1
            else:
                rejected += 1
                b.setdefault("rejected", []).extend(verdict.problems[:3])
    if accepted:
        changes.append({"section": "All", "type": "rewritten", "detail": f"Reworded {accepted} item(s) to emphasise "
                                                                         "the job's priorities"})
    if rejected:
        changes.append({"section": "All", "type": "kept", "detail": f"Kept original wording for {rejected} item(s) "
                                                                    "where the rewrite added unsupported claims"})
    return accepted > 0


def _json(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=1)


def to_render_instructions(content: dict) -> list[dict]:
    out = []
    for sec in content["sections"]:
        if sec.get("unchanged"):
            continue
        ins = {"index": sec["index"]}
        if sec["layout"] == "paragraph":
            ins["text"] = sec.get("text", "")
        elif sec["layout"] == "entries":
            ins["entries"] = [
                {"source_index": e.get("source_index"), "fields": e.get("fields", {}),
                 "bullets": None if e.get("bullets") is None else [b["text"] for b in e["bullets"]]}
                for e in sec["entries"]
            ]
        elif sec["layout"] == "skills":
            ins["categories"] = [{"name": c["name"], "items": c["items"]} for c in sec["categories"]]
        elif sec["layout"] == "items":
            ins["items"] = sec["items"]
        out.append(ins)
    return out


def trim_for_length(content: dict, step: int) -> str | None:
    """Removes the least relevant content, one step at a time. Returns a description, or None when nothing more
    can be removed without dropping whole sections."""
    projects = next((s for s in content["sections"] if s["kind"] == "projects" and s.get("entries")), None)
    exps = [s for s in content["sections"] if s["kind"] == "experience" and s.get("entries")]
    if projects:
        for e in reversed(projects["entries"]):
            if e.get("bullets") and len(e["bullets"]) > 2:
                removed = e["bullets"].pop()
                return f"Removed a bullet from {e['fields'].get('primary', 'a project')}: \"{removed['text'][:60]}...\""
        if len(projects["entries"]) > 2:
            e = projects["entries"].pop()
            return f"Removed project {e['fields'].get('primary', '')} to fit the page limit"
    for s in exps:
        for e in s["entries"]:
            if e.get("bullets") and len(e["bullets"]) > 2:
                removed = e["bullets"].pop()
                return f"Removed a bullet from {e['fields'].get('primary', '')}: \"{removed['text'][:60]}...\""
    if projects:
        for e in reversed(projects["entries"]):
            if e.get("bullets") and len(e["bullets"]) > 1:
                removed = e["bullets"].pop()
                return f"Removed a bullet from {e['fields'].get('primary', '')}"
    return None
