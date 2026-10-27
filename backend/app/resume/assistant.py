"""Shared matching helpers for the resume agent (app.agent.resume_agent) and the change preview."""

import copy
import re
from dataclasses import dataclass, field

from app.resume.document import index


@dataclass
class Plan:
    ops: list[dict] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    message: str = ""
    broad: bool = False  # needs a preview before applying
    refused: list[str] = field(default_factory=list)


LINK_WORDS = {"portfolio": r"portfolio|personal (?:web)?site|website", "github": r"git ?hub", "linkedin": r"linked ?in"}
SECTION_WORDS = {
    "summary": r"summary|profile|objective",
    "projects": r"projects?",
    "experience": r"experience|internships?|work",
    "skills": r"skills?",
    "education": r"education",
    "certifications": r"certifications?",
    "achievements": r"achievements?|awards?",
}
REWRITE = re.compile(
    r"\b(shorten|shorter|concise|trim|tighten|natural|stronger|improve|rewrite|reword|rephrase|"
    r"emphasi[sz]e|focus|polish|clean ?up|simplify|clearer|less generic|two lines|2 lines|one line|1 line|"
    r"make .* (?:better|backend|technical))\b",
    re.I,
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _entries(content: dict, kind: str | None = None) -> list[tuple[dict, dict]]:
    return [
        (s, e)
        for s in content["sections"]
        if not s.get("removed") and (kind is None or s["kind"] == kind)
        for e in s.get("entries") or []
    ]


def _match_entry(content: dict, text: str) -> tuple[dict, dict] | None:
    t = _norm(text)
    best = None
    for s, e in _entries(content):
        for cand in (e["fields"].get("short_name", ""), e["fields"].get("primary", "")):
            c = _norm(cand)
            matches = c and (c in t or (len(t) >= 4 and t in c))
            if matches and (best is None or len(c) > len(_norm(best[1]["fields"].get("short_name", "")))):
                best = (s, e)
    return best


def _sections_named(content: dict, low: str) -> list[dict]:
    found = []
    for s in content["sections"]:
        rx = SECTION_WORDS.get(s["kind"])
        title = (s.get("title_text") or s["title"]).lower()
        if (rx and re.search(rf"\b(?:{rx})\b", low)) or re.search(rf"\b{re.escape(title)}\b", low):
            found.append(s)
    return found


def preview(content: dict, ops: list[dict]) -> list[dict]:
    """Before/after text for a proposed change, for the preview panel."""
    from app.resume.document import apply_ops

    before = index(content)
    after_doc, changed = apply_ops(copy.deepcopy(content), ops, "assistant")
    after = index(after_doc)
    out = []
    for cid in changed:
        out.append(
            {
                "id": cid,
                "before": (before.get(cid) or {}).get("text", ""),
                "after": (after.get(cid) or {}).get("text", ""),
            }
        )
    return out
