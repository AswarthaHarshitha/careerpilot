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
    config: dict = field(default_factory=dict)  # effective job instructions (app.resume.instructions)
    guidance: list[str] = field(default_factory=list)  # free-text preferences from the user for the model


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


IMPERATIVE = {
    "import",
    "create",
    "track",
    "manage",
    "view",
    "upload",
    "search",
    "export",
    "send",
    "detect",
    "generate",
    "sync",
    "connect",
    "add",
    "edit",
    "delete",
    "schedule",
    "book",
    "browse",
    "filter",
    "compare",
    "download",
    "share",
    "monitor",
    "run",
    "build",
    "chat",
    "ask",
    "record",
    "convert",
    "analyze",
    "analyse",
    "review",
}


def _gerund(verb: str) -> str:
    v = verb.lower()
    if v.endswith("e") and not v.endswith("ee"):
        return v[:-1] + "ing"
    if v in ("run", "chat"):
        return v + v[-1] + "ing"
    return v + "ing"


def _clean_description(desc: str) -> str:
    d = re.sub(r"\s*\([^)]*\)\s*$", "", desc.strip().rstrip("."))  # trailing "(FastAPI, React)" tech lists
    d = re.split(r"\s+[-\u2013\u2014]\s+|;\s+|\.\s+", d, maxsplit=1)[0]
    d = d.replace(": ", " with ", 1) if ": " in d else d
    words = d.split()
    if len(words) > 24:  # cut at the last clause boundary within the limit, never mid-phrase
        clipped = " ".join(words[:24])
        cut = max(clipped.rfind(","), clipped.rfind(" and "))
        d = clipped[:cut] if cut > 40 else clipped
    return d.strip(" ,") if words else ""


def evidence_bullets(p: ProjectInput, focus: JobFocus) -> list[str]:
    """Bullets for a project with no user-written bullets, assembled only from verified facts: the repository
    description the user wrote and its verified technologies and README features. Marked for review in the UI."""
    out = []
    tech = p.tech[:5]
    desc = _clean_description(p.description)
    using = f" using {_join(tech)}" if tech else ""
    if desc and not re.search(r"\byou(r|rs)?\b", desc, re.I):
        lead = desc[0].lower() + desc[1:]
        article = "" if re.match(r"(a|an|the)\s", lead) else ("an " if lead[:1] in "aeiou" else "a ")
        out.append(f"Built {article}{lead}{using}.")
    elif tech:
        out.append(f"Built {p.title}{using}.")
    for f in sorted(p.features, key=lambda f: -relevance(f, focus))[:2]:
        f = f.strip().rstrip(".")
        if re.search(r"\byou(r|rs)?\b", f, re.I) and len(f.split()) > 3:
            continue
        first, _, rest = f.partition(" ")
        if first.lower() in IMPERATIVE:
            out.append(f"Supports {_gerund(first)} {rest}.")
        elif first.lower().endswith("ed"):
            out.append(f[0].upper() + f[1:] + ".")
        else:
            out.append(f"Implemented {f[0].lower() + f[1:]}.")
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


TECH_TEXT = re.compile(r"^\s*(\*\*)?\s*(technologies|tech stack|tools|stack|built with)\s*:?(\*\*)?\s*:?", re.I)


def _tech_bullet(line: str, focus: "JobFocus") -> dict:
    """The "Technologies:" line: kept last, never reworded; its items reordered by relevance to the job."""
    m = TECH_TEXT.match(line)
    label = re.sub(r"[*:]", "", m.group(0)).strip() if m else "Technologies"
    items = [x.strip().rstrip(".") for x in re.split(r",\s*", line[m.end() :] if m else line) if x.strip()]
    items.sort(key=lambda t: -relevance(t, focus))
    text = f"**{label}:** " + ", ".join(items) + "."
    return {"text": text, "original": text, "origin": "profile", "tech_line": True}


CERT_ITEM = re.compile(r"certif|certificate|licen[cs]e|\bcertified\b", re.I)


def apply_emphasis(focus: JobFocus, emphasis: dict[str, float]) -> JobFocus:
    """Job instructions such as "emphasize backend" / "don't emphasize frontend" reweight what counts as relevant."""
    from app.resume.instructions import EMPHASIS_AREAS

    if not emphasis:
        return focus
    weights = dict(focus.weights)
    for area, w in emphasis.items():
        cats = set(EMPHASIS_AREAS.get(area, ()))
        for key, sk in SKILLS.items():
            if sk.category not in cats:
                continue
            if w > 0:
                weights[key] = weights.get(key, 0.0) * 1.6 + 0.6
            else:
                weights[key] = 0.0
    ordered = sorted(dict.fromkeys(focus.ordered), key=lambda k: -weights.get(k, 0))
    return JobFocus(weights, ordered, focus.role, focus.company)


def order_projects(projects: list["ProjectInput"], priority: list[str], limit: int) -> list["ProjectInput"]:
    def rank(p: "ProjectInput") -> int:
        for i, name in enumerate(priority):
            if _norm(name) and (_norm(name) in _norm(p.title) or _norm(p.title) in _norm(name)):
                return i
        return len(priority)

    ordered = sorted(projects, key=lambda p: (rank(p), -p.score))
    return ordered[: max(1, limit)] if limit else ordered


def tailor(inputs: TailorInputs, provider: LLMProvider | None = None) -> tuple[dict, list[dict]]:
    cfg = inputs.config or {}
    focus = apply_emphasis(inputs.focus, cfg.get("emphasis") or {})
    flags = set(cfg.get("flags") or [])
    changes: list[dict] = []
    sections_out = []
    rewrite_jobs: list[dict] = []  # bullets and summary eligible for model rewording
    if cfg:
        inputs.projects = order_projects(inputs.projects, cfg.get("project_priority") or [], cfg.get("projects") or 0)

    for sec in inputs.structure["sections"]:
        kind, layout = sec["kind"], sec["layout"]
        base = {"index": sec["index"], "title": sec["title"], "kind": kind, "layout": layout}

        if kind in ("summary", "certifications") and cfg.get(kind) == "exclude":
            base["removed"] = True
            changes.append(
                {
                    "section": sec["title"],
                    "type": "removed",
                    "detail": f"Removed {sec['title']} (your instruction for this job)",
                }
            )
            sections_out.append(base)
            continue

        if kind == "summary" and layout == "paragraph":
            original = sec["paragraph"]["text"]
            base.update({"text": original, "original_text": original})
            if "keep_original_summary" not in flags:
                rewrite_jobs.append(
                    {"type": "summary", "section": base, "source": original + "\n" + inputs.profile_summary}
                )
            sections_out.append(base)

        elif kind == "experience" and layout == "entries":
            entries = []
            for i, e in enumerate(sec["entries"]):
                exp = _find_experience(inputs.experiences, e["fields"])
                all_bullets = exp.bullets if exp and exp.bullets else [it["text"] for it in e["items"]]
                tech = next((b for b in all_bullets if TECH_TEXT.match(b)), None)
                bullets = [b for b in all_bullets if not TECH_TEXT.match(b)]
                if not bullets:
                    entries.append(
                        {
                            "source_index": i,
                            "fields": e["fields"],
                            "bullets": None,
                            "ref": {"type": "experience", "id": exp.id} if exp else None,
                        }
                    )
                    continue
                ordered = sorted(bullets, key=lambda b: -relevance(b, focus))
                entry = {
                    "source_index": i,
                    "fields": e["fields"],
                    "ref": {"type": "experience", "id": exp.id} if exp else None,
                    "bullets": [{"text": b, "original": b, "origin": "profile"} for b in ordered],
                }
                if tech:
                    entry["bullets"].append(_tech_bullet(tech, focus))
                if ordered != bullets:
                    changes.append(
                        {
                            "section": sec["title"],
                            "type": "reordered",
                            "detail": f"Put the most relevant bullets first for {e['fields'].get('primary', '')}",
                        }
                    )
                allowed = allowed_skills_for(bullets, set())
                for b in entry["bullets"]:
                    if b.get("tech_line"):
                        continue
                    rewrite_jobs.append(
                        {
                            "type": "bullet",
                            "bullet": b,
                            "source": "\n".join(bullets),
                            "allowed": allowed,
                            "context": e["fields"].get("primary", ""),
                        }
                    )
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
                tech_line = None
                template_items = sec["entries"][idx]["items"] if idx is not None else []
                if p.bullets:
                    pool = [(b, "profile") for b in p.bullets if not TECH_TEXT.match(b)]
                    tech_line = next((b for b in p.bullets if TECH_TEXT.match(b)), None)
                elif template_items:
                    pool = [(it["text"], "template") for it in template_items if not it.get("tech_line")]
                    tech_line = next((it["text"] for it in template_items if it.get("tech_line")), None)
                else:
                    pool = [(b, "evidence") for b in evidence_bullets(p, focus)]
                uses_tech_lines = any(it.get("tech_line") for e2 in sec["entries"] for it in e2["items"])
                if uses_tech_lines and not tech_line and p.tech:
                    tech_line = "Technologies: " + ", ".join(p.tech[:6])  # only verified technologies
                if not pool:
                    continue
                ranked = sorted(pool, key=lambda bo: -relevance(bo[0], focus))
                keep = ranked[: max(1, inputs.max_bullets_per_project)]
                # keep the user's original order among the chosen bullets for readability
                keep.sort(key=lambda bo: pool.index(bo))
                fields = (
                    dict(sec["entries"][idx]["fields"])
                    if idx is not None
                    else {
                        "primary": p.title,
                        "url": p.url,
                        "tech": ", ".join(p.tech[:5]),
                    }
                )
                entry = {
                    "source_index": idx,
                    "fields": fields,
                    "ref": {"type": "project", "id": p.id},
                    "matched": p.matched[:8],
                    "bullets": [{"text": b, "original": b, "origin": o} for b, o in keep],
                }
                if tech_line:
                    entry["bullets"].append(_tech_bullet(tech_line, focus))
                allowed = p.claimable | allowed_skills_for([b for b, _ in pool], set())
                source = "\n".join([p.description] + [b for b, _ in pool] + p.features)
                for b in entry["bullets"]:
                    if b.get("tech_line"):
                        continue
                    rewrite_jobs.append(
                        {"type": "bullet", "bullet": b, "source": source, "allowed": allowed, "context": p.title}
                    )
                entries.append(entry)
                why = ", ".join(p.matched[:5]) or "related evidence"
                changes.append(
                    {"section": sec["title"], "type": "selected", "detail": f"Included {p.title} - evidence for {why}"}
                )
                if any(o == "evidence" for _, o in keep):
                    changes.append(
                        {
                            "section": sec["title"],
                            "type": "drafted",
                            "detail": f"{p.title}: bullets drafted from the repository description and "
                            "verified evidence - review the wording",
                        }
                    )
            dropped = [e["fields"].get("primary", "") for i, e in enumerate(sec["entries"]) if i not in used_template]
            for d in dropped:
                if d:
                    changes.append(
                        {
                            "section": sec["title"],
                            "type": "removed",
                            "detail": f"Left out {d} (less relevant to this job)",
                        }
                    )
            if not entries:  # nothing selected - keep the template's projects as they are
                entries = [
                    {"source_index": i, "fields": e["fields"], "bullets": None} for i, e in enumerate(sec["entries"])
                ]
            base["entries"] = entries
            sections_out.append(base)

        elif layout == "skills":
            cats, skill_changes = _tailor_skills(sec["skills"], inputs, focus, "relevant_skills_only" in flags)
            base["categories"] = cats
            changes += [{"section": sec["title"], **c} for c in skill_changes]
            sections_out.append(base)

        elif layout == "items":
            items = [it["text"] for it in sec["items"]]
            if cfg.get("certifications") == "exclude":
                kept = [t for t in items if not CERT_ITEM.search(t)]
                if len(kept) < len(items):
                    changes.append(
                        {
                            "section": sec["title"],
                            "type": "removed",
                            "detail": f"Removed {len(items) - len(kept)} certification item(s) (your instruction)",
                        }
                    )
                if not kept:
                    base["removed"] = True
                    sections_out.append(base)
                    continue
                items = kept
            ordered = sorted(items, key=lambda t: -relevance(t, focus))
            base["items"] = ordered
            base["items_unchanged"] = ordered == [it["text"] for it in sec["items"]]
            if ordered != [it["text"] for it in sec["items"]] and len(ordered) == len(sec["items"]):
                changes.append({"section": sec["title"], "type": "reordered", "detail": "Most relevant items first"})
            sections_out.append(base)
        else:
            base["unchanged"] = True
            sections_out.append(base)

    used_llm = False
    if provider is not None and rewrite_jobs:
        used_llm = _llm_rewrite(
            provider, rewrite_jobs, focus, changes, guidance=inputs.guidance, concise_summary="concise_summary" in flags
        )

    content = {"sections": sections_out, "focus": focus.ordered[:15], "used_llm": used_llm}
    return content, changes


def _find_experience(exps: list[ExperienceInput], fields: dict) -> ExperienceInput | None:
    org = _norm(fields.get("primary", ""))
    for e in exps:
        o = _norm(e.organization)
        if o and org and (o == org or o in org or org in o):
            return e
    return None


def _tailor_skills(
    categories: list[dict], inputs: TailorInputs, focus: JobFocus, relevant_only: bool = False
) -> tuple[list[dict], list[dict]]:
    changes = []
    out = []
    listed_keys = set()
    for c in categories:
        for item in c["items"]:
            for m in find_skills(item):
                listed_keys.add(m.key)
    # JD skills the user can back up - demonstrated in projects, or listed in their own profile - that the skills
    # section does not mention yet. A skill the user never claimed is not added.
    own = set(inputs.profile_skills)
    additions = [
        k for k in focus.ordered if (k in inputs.strong_skills or k in own) and k not in listed_keys and k in SKILLS
    ]
    placed: set[str] = set()
    cat_targets = []
    for c in categories:
        accepts = set()
        for rx, cats in CATEGORY_HINTS:
            if rx.search(c["name"]):
                accepts |= cats
        cat_targets.append(accepts)
    # Each addition goes to the first category that accepts it; one no category accepts goes to a general row
    # ("Tools", "Other") or the last one, so a skill the user has is never silently dropped.
    general = next(
        (i for i, c in enumerate(categories) if re.search(r"tool|other|additional|misc", c["name"], re.I)),
        len(categories) - 1,
    )
    assigned: dict[int, list[str]] = {}
    for k in additions:
        i = next((i for i, acc in enumerate(cat_targets) if category_of(k) in acc), general)
        if i >= 0:
            assigned.setdefault(i, []).append(k)
    for i, (c, _accepts) in enumerate(zip(categories, cat_targets, strict=True)):
        items = list(c["items"])

        def score(item: str) -> float:
            return relevance(item, focus)

        ordered = sorted(items, key=lambda it: -score(it))
        added = []
        for k in assigned.get(i, []):
            if k in placed:
                continue
            added.append(display_name(k))
            placed.add(k)
        new_items = ordered + added
        if relevant_only:
            keep = [it for it in new_items if score(it) > 0] or new_items[:1]
            if len(keep) < len(new_items):
                changes.append(
                    {
                        "type": "removed",
                        "detail": f"{c['name']}: left out {len(new_items) - len(keep)} "
                        "skill(s) not relevant to this job (your instruction)",
                    }
                )
            new_items = keep
        if ordered != items:
            moved = [it for it in ordered[:3] if score(it) > 0]
            if moved:
                changes.append({"type": "reordered", "detail": f"{c['name']}: moved {', '.join(moved)} to the front"})
        if added:
            changes.append(
                {"type": "added", "detail": f"{c['name']}: added {', '.join(added)} (from your profile and projects)"}
            )
        out.append({"name": c["name"], "items": new_items, "original": items})
    return out, changes


LLM_SYSTEM = (
    "You tailor resume wording for one job. For each item, rewrite it to emphasise what matters for the job, "
    "keeping every fact identical. Write like an engineer describing their own work: action + what was built or "
    "done + technology or method + a result only if the original states one. Rules: do not add technologies, "
    "numbers, users, customers, scale, impact, leadership or awards that the original does not state; keep each "
    "bullet under 30 words; start bullets with a specific past-tense verb and vary the verbs; no first person; no "
    "marketing language or filler such as leveraged, cutting-edge, spearheaded, harnessed, seamlessly, robust, "
    "innovative, passionate, results-driven, state-of-the-art, pivotal, utilized, synergy; do not copy sentences "
    "from the job description; do not repeat a keyword just to include it. If an item cannot be improved, return "
    "it unchanged. "
    'Return JSON: {"items": {"<id>": "<rewritten text>"}}'
)


def _llm_rewrite(
    provider: LLMProvider,
    jobs: list[dict],
    focus: JobFocus,
    changes: list[dict],
    *,
    guidance: list[str] | None = None,
    concise_summary: bool = False,
    fix_notes: str = "",
) -> bool:
    items = {}
    for n, job in enumerate(jobs):
        key = f"i{n}"
        job["key"] = key
        if job["type"] == "summary":
            items[key] = {"kind": "summary", "text": job["section"]["text"]}
        else:
            items[key] = {"kind": "bullet", "entry": job["context"], "text": job["bullet"]["text"]}
    focus_names = ", ".join(display_name(k) for k in focus.ordered[:15])
    extra = ""
    if guidance:
        extra += (
            "The candidate's preferences for this application (style and emphasis only; they never permit new "
            "facts):\n" + wrap_untrusted("PREFERENCES", "\n".join(guidance), 2000) + "\n"
        )
    if concise_summary:
        extra += "Keep the summary to at most two short sentences.\n"
    if fix_notes:
        extra += f"Fix these problems in the items: {fix_notes}\n"
    prompt = (
        f"Target role: {focus.role or 'not stated'} at {focus.company or 'the company'}.\n"
        f"Skills the job emphasises (in order): {focus_names}.\n" + extra + "\n"
        "Items to rewrite (JSON):\n" + wrap_untrusted("RESUME ITEMS", _json(items), 20_000)
    )
    try:
        data = provider.complete_json(LLM_SYSTEM, prompt, max_tokens=4000, temperature=0.3)
    except LLMError as exc:
        changes.append(
            {
                "section": "All",
                "type": "note",
                "detail": f"Model rewording unavailable ({exc}); kept your original wording.",
            }
        )
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
                changes.append(
                    {"section": sec["title"], "type": "rewritten", "detail": "Summary aligned with the role"}
                )
            else:
                rejected += 1
                changes.append(
                    {
                        "section": sec["title"],
                        "type": "kept",
                        "detail": "Kept your summary: the suggested rewrite " + "; ".join(verdict.problems[:2]),
                    }
                )
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
        changes.append(
            {
                "section": "All",
                "type": "rewritten",
                "detail": f"Reworded {accepted} item(s) to emphasise the job's priorities",
            }
        )
    if rejected:
        changes.append(
            {
                "section": "All",
                "type": "kept",
                "detail": f"Kept original wording for {rejected} item(s) where the rewrite added unsupported claims",
            }
        )
    return accepted > 0


def _json(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=1)


def removed_sections(content: dict) -> list[int]:
    return [s["index"] for s in content["sections"] if s.get("removed")]


def section_titles(content: dict) -> dict[int, str]:
    return {s["index"]: s["title_text"] for s in content["sections"] if s.get("title_text")}


def polish(content: dict, provider: LLMProvider, flagged: list[str], changes: list[dict]) -> int:
    """Rewrites only the bullets the quality check flagged (generic wording, first person, tense), from their own
    source text, under the same truth checks. Returns how many were improved."""
    jobs = []
    for sec in content["sections"]:
        if sec.get("removed"):
            continue
        for e in sec.get("entries") or []:
            for b in e.get("bullets") or []:
                if b["text"] in flagged and b.get("origin") not in ("user",):
                    source = b.get("original", "") + "\n" + b["text"]
                    jobs.append(
                        {
                            "type": "bullet",
                            "bullet": b,
                            "source": source,
                            "allowed": allowed_skills_for([source], set()),
                            "context": e.get("fields", {}).get("primary", ""),
                        }
                    )
    if not jobs:
        return 0
    before = [j["bullet"]["text"] for j in jobs]
    _llm_rewrite(
        provider,
        jobs,
        JobFocus({}, []),
        changes,
        fix_notes="remove generic or inflated wording, first person and present tense; keep every fact",
    )
    return sum(1 for j, old in zip(jobs, before, strict=True) if j["bullet"]["text"] != old)


def to_render_instructions(content: dict) -> list[dict]:
    out = []
    for sec in content["sections"]:
        if sec.get("unchanged") or sec.get("removed"):
            continue
        ins = {"index": sec["index"]}
        if sec["layout"] == "paragraph":
            ins["text"] = sec.get("text", "")
        elif sec["layout"] == "entries":
            ins["entries"] = [
                {
                    "source_index": e.get("source_index"),
                    "fields": e.get("fields", {}),
                    "rewrite_heading": bool(e.get("rewrite_heading")),
                    "bullets": None if e.get("bullets") is None else [b["text"] for b in e["bullets"]],
                }
                for e in sec["entries"]
            ]
        elif sec["layout"] == "skills":
            ins["categories"] = [{"name": c["name"], "items": c["items"]} for c in sec["categories"]]
        elif sec["layout"] == "entries" and not sec.get("entries"):
            continue
        elif sec["layout"] == "items":
            if sec.get("items_unchanged"):
                continue
            ins["items"] = sec["items"]
        out.append(ins)
    return out


def _content_bullets(e: dict) -> int:
    return sum(1 for b in e.get("bullets") or [] if not b.get("tech_line"))


def _pop_content_bullet(e: dict) -> dict:
    for i in range(len(e["bullets"]) - 1, -1, -1):
        if not e["bullets"][i].get("tech_line"):
            return e["bullets"].pop(i)
    return e["bullets"].pop()


def trim_for_length(content: dict, step: int, keep_projects: int = 0) -> str | None:
    """Removes the least relevant content, one step at a time. Returns a description, or None when nothing more
    can be removed without dropping whole sections."""
    projects = next((s for s in content["sections"] if s["kind"] == "projects" and s.get("entries")), None)
    exps = [s for s in content["sections"] if s["kind"] == "experience" and s.get("entries")]
    if projects:
        for e in reversed(projects["entries"]):
            if e.get("bullets") and _content_bullets(e) > 2:
                removed = _pop_content_bullet(e)
                return f'Removed a bullet from {e["fields"].get("primary", "a project")}: "{removed["text"][:60]}..."'
        if len(projects["entries"]) > max(2, keep_projects):
            e = projects["entries"].pop()
            return f"Removed project {e['fields'].get('primary', '')} to fit the page limit"
    for s in exps:
        for e in s["entries"]:
            if e.get("bullets") and _content_bullets(e) > 2:
                removed = _pop_content_bullet(e)
                return f'Removed a bullet from {e["fields"].get("primary", "")}: "{removed["text"][:60]}..."'
    if projects:
        for e in reversed(projects["entries"]):
            if e.get("bullets") and _content_bullets(e) > 1:
                removed = _pop_content_bullet(e)
                return f"Removed a bullet from {e['fields'].get('primary', '')}"
    return None
