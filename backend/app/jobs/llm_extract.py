"""Optional language-model pass over a job description.

The model may add requirements the deterministic parser missed and fill company/role/job-id gaps, but every
value it returns is checked against the posting text: a skill must be mentioned there, and a job id or
deadline must appear verbatim. Anything else is discarded.
"""

import logging
import re

from app.jobs.parse import IMPORTANCE_RANK, JDAnalysis, Requirement, parse_explicit_date
from app.llm.provider import LLMError, LLMProvider, wrap_untrusted
from app.matching.taxonomy import canonicalize, category_of, display_name, find_skills

log = logging.getLogger(__name__)

SYSTEM = (
    "Extract structured facts from one job posting. Use only what the posting says. Return JSON with keys: "
    '"company", "role", "job_id", "location", "employment_type", "workplace_type" (onsite|hybrid|remote|""), '
    '"required_skills" (list of short skill names), "preferred_skills" (list), "responsibilities" (list of short '
    'sentences), "min_years_experience" (number or null), "education" (string), '
    '"deadline_quote" (the exact sentence stating an application deadline, or "" if none is stated). '
    "Do not guess; use empty values when the posting does not say."
)


def enhance_with_llm(provider: LLMProvider, text: str, analysis: JDAnalysis, meta: dict) -> list[str]:
    """Merges model output into `analysis` and `meta` in place. Returns notes about what was used."""
    try:
        data = provider.complete_json(SYSTEM, wrap_untrusted("JOB POSTING", text, 24_000), max_tokens=2000)
    except LLMError as exc:
        log.info("JD LLM pass failed: %s", exc)
        return [f"Model-assisted analysis was unavailable ({exc}); deterministic analysis was used."]
    low = text.lower()
    notes = []
    present = {m.key for m in find_skills(text)}
    existing = {r.canonical: r for r in analysis.requirements if r.kind == "skill"}
    added = 0
    for field, importance in (("required_skills", "required"), ("preferred_skills", "preferred")):
        for name in (data.get(field) or [])[:40]:
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()[:80]
            key = canonicalize(name)
            mentioned = key in present or name.lower() in low
            if not mentioned:
                continue  # the model named something the posting does not mention
            if key in existing:
                cur = existing[key]
                if IMPORTANCE_RANK[importance] < IMPORTANCE_RANK[cur.importance]:
                    cur.importance = importance
                continue
            label = display_name(key) if not key.startswith("x:") else name
            req = Requirement("skill", label, key, category_of(key) if not key.startswith("x:") else "other",
                              importance, _snippet(text, name))
            analysis.requirements.append(req)
            existing[key] = req
            added += 1
    if added:
        notes.append(f"Model-assisted analysis added {added} requirement(s) found in the posting text.")

    for key, attr in (("company", "company"), ("role", "role_title"), ("location", "location")):
        val = str(data.get(key) or "").strip()
        if val and not meta.get(attr) and val.lower() in low:
            meta[attr] = val[:300]
    jid = str(data.get("job_id") or "").strip()
    if jid and not analysis.job_code and jid in text and re.search(r"\d", jid):
        analysis.job_code = jid[:120]
    if not analysis.employment_type and data.get("employment_type"):
        et = str(data["employment_type"])
        if et.lower() in low:
            analysis.employment_type = et[:60]
    if not analysis.workplace_type and data.get("workplace_type") in ("onsite", "hybrid", "remote"):
        if data["workplace_type"] in low or (data["workplace_type"] == "onsite" and "on-site" in low):
            analysis.workplace_type = data["workplace_type"]
    if not analysis.responsibilities:
        analysis.responsibilities = [str(r)[:300] for r in (data.get("responsibilities") or [])[:12]
                                     if isinstance(r, str)]
    quote = str(data.get("deadline_quote") or "").strip()
    if quote and not analysis.deadline and _normalize(quote) in _normalize(text):
        d = parse_explicit_date(quote)
        if d:
            analysis.deadline = d
            analysis.deadline_source = f'Stated in the posting: "{quote[:120]}"'
    return notes


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _snippet(text: str, name: str) -> str:
    i = text.lower().find(name.lower())
    if i < 0:
        return ""
    start = max(text.rfind("\n", 0, i), 0)
    end = text.find("\n", i)
    return text[start : end if end > 0 else len(text)].strip(" -\n")[:300]
