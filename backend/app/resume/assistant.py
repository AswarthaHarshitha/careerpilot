"""Turns an editing request ("put Resolve first", "shorten this bullet") into targeted document operations.

The assistant never regenerates the resume. It decides the scope of the request (the selected element, a named
section, or the whole resume), produces operations for exactly those elements, and leaves everything else alone.
Text the user typed themselves is not rewritten unless they selected it or explicitly include their edits.
Rewording uses the configured model under the same truth checks as generation; structural requests (links,
order, sections, page target) are handled deterministically.
"""

import copy
import json
import re
from dataclasses import dataclass, field

from app.llm.provider import LLMError, LLMProvider, wrap_untrusted
from app.matching.taxonomy import find_skills
from app.resume.document import index
from app.resume.truth import allowed_skills_for, check_rewrite


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


def plan(
    content: dict,
    instruction: str,
    selection: list[str],
    provider: LLMProvider | None,
    jd_focus: list[str] | None = None,
) -> Plan:
    p = Plan()
    text = instruction.strip()
    low = text.lower()
    if not text:
        p.message = "Tell me what to change."
        return p
    elements = index(content)

    # explicit replacement: 'replace this with: "..."' / 'change it to ...'
    m = re.search(
        r"(?:replace|change|set)\s+(?:this|it|the selected \w+|this \w+)?\s*(?:sentence|bullet|text|line)?\s*"
        r"(?:with|to)\s*[:\-]?\s*[\"“]?(.+?)[\"”]?\s*$",
        text,
        re.I,
    )
    if m and selection and len(selection) == 1 and selection[0] in elements:
        p.ops.append({"op": "update_text", "id": selection[0], "text": m.group(1).strip()})
        p.summary.append("Replace the selected text with your wording")
        return p

    # links
    for kind, words in LINK_WORDS.items():
        if re.search(rf"\b(?:{words})\b", low):
            clause = re.split(r"\bbut\b|,|;", low)
            for part in clause:
                if re.search(rf"\b(?:{words})\b", part):
                    if re.search(r"\b(remove|hide|drop|delete|exclude|without|no|don'?t|take out|get rid of)\b", part):
                        p.ops.append({"op": "set_link", "kind": kind, "mode": "exclude"})
                        p.summary.append(f"Remove the {kind} link")
                    elif re.search(r"\b(add|include|show|keep|put|restore|bring back)\b", part):
                        p.ops.append({"op": "set_link", "kind": kind, "mode": "include"})
                        p.summary.append(f"Show the {kind} link")
    if p.ops and not REWRITE.search(low):
        return p

    # ordering: "put Resolve first", "move X above Y", "X second"
    if re.search(r"\b(first|top|above|before|below|after|second|third|last|move|reorder|put)\b", low):
        target = _match_entry(content, re.split(r"\b(above|before|below|after)\b", low)[0])
        if target:
            sec, e = target
            entries = sec["entries"]
            pos = None
            if re.search(r"\b(first|top|lead)\b", low):
                pos = 0
            elif re.search(r"\bsecond\b", low):
                pos = 1
            elif re.search(r"\bthird\b", low):
                pos = 2
            elif re.search(r"\blast\b|\bbottom\b", low):
                pos = len(entries) - 1
            else:
                other_m = re.search(r"\b(above|before|below|after)\b(.+)$", low)
                other = _match_entry(content, other_m.group(2)) if other_m else None
                if other and other[1] is not e:
                    j = [x["id"] for x in entries if x is not e].index(other[1]["id"])
                    pos = j if other_m.group(1) in ("above", "before") else j + 1
            if pos is not None:
                p.ops.append({"op": "move_entry", "id": e["id"], "position": pos})
                p.summary.append(
                    f"Move {e['fields'].get('short_name') or e['fields'].get('primary')} to position {pos + 1}"
                )
                return p

    # page target
    m = re.search(r"\b(one|1|two|2)[\s-]pages?\b", low)
    if m:
        pages = 1 if m.group(1) in ("one", "1") else 2
        p.ops.append({"op": "set_page_target", "pages": pages})
        p.summary.append(f"Set the page target to {pages}")
        p.broad = True
        p.message = (
            "I'll fit the resume to the page by removing the least relevant content first; the full list of "
            "changes appears after applying, and you can undo it."
        )
        return p

    # remove / restore whole sections or entries
    if re.search(r"\b(remove|delete|drop|hide|get rid of|take out)\b", low) and not REWRITE.search(low):
        ent = _match_entry(content, low)
        secs = _sections_named(content, low)
        if ent and not secs:
            p.ops.append({"op": "remove_entry", "id": ent[1]["id"]})
            p.summary.append(f"Remove {ent[1]['fields'].get('primary')}")
            p.broad = True
            return p
        for s in secs:
            p.ops.append({"op": "remove_section", "id": s["id"]})
            p.summary.append(f"Remove the {s.get('title_text') or s['title']} section")
        if secs:
            p.broad = True
            return p
    if re.search(r"\b(restore|bring back|add back|put back)\b", low):
        for s in content["sections"]:
            if s.get("removed") and s in _sections_named(content, low):
                p.ops.append({"op": "restore_section", "id": s["id"]})
                p.summary.append(f"Restore the {s['title']} section")
        for s in content["sections"]:
            for e in s.get("removed_entries") or []:
                if _norm(e["fields"].get("short_name", "")) in _norm(low):
                    p.ops.append({"op": "restore_entry", "id": e["id"]})
                    p.summary.append(f"Restore {e['fields'].get('primary')}")
        if p.ops:
            return p

    # skills: add/remove a named skill
    sk = re.search(r"\b(add|remove|drop)\b\s+(.+?)\s+(?:to|from)\s+(?:the\s+)?skills", low)
    if sk:
        return _skills_change(content, sk.group(1), sk.group(2), p)

    # rewriting
    if REWRITE.search(low):
        return _rewrite(content, text, selection, provider, jd_focus or [], p)

    p.message = (
        "I couldn't turn that into a specific change. Try things like “put Resolve first”, “remove the "
        "portfolio link”, “shorten the summary”, or select a bullet and say “make this more natural”."
    )
    return p


def _skills_change(content: dict, action: str, name: str, p: Plan) -> Plan:
    cats = [c for s in content["sections"] for c in s.get("categories") or []]
    if action in ("remove", "drop"):
        for c in cats:
            keep = [x for x in c["items"] if _norm(x) != _norm(name)]
            if len(keep) != len(c["items"]):
                p.ops.append({"op": "set_skills", "id": c["id"], "items": keep or c["items"][:1]})
                p.summary.append(f"Remove {name} from {c['name']}")
        if not p.ops:
            p.message = f"{name} is not in your skills section."
        return p
    p.refused.append(
        f"Adding “{name}” needs evidence. Add it to your profile skills or a project first; the "
        "resume only lists skills your profile or verified evidence supports."
    )
    p.message = p.refused[-1]
    return p


def _targets(content: dict, low: str, selection: list[str], include_user_edits: bool) -> list[tuple[str, dict]]:
    elements = index(content)
    secs = _sections_named(content, low)
    named_ids = {s["id"] for s in secs}
    # a section named in the request wins over a selection left over from earlier editing
    if selection and not (named_ids and not any(elements.get(i, {}).get("section") in named_ids for i in selection)):
        return [
            (i, elements[i])
            for i in selection
            if i in elements and elements[i]["type"] in ("bullet", "paragraph", "item")
        ]
    whole = re.search(r"\b(whole|entire|everything|all|resume)\b", low) and not secs
    out = []
    for eid, el in elements.items():
        if el["type"] not in ("bullet", "paragraph"):
            continue
        if el.get("tech_line"):
            continue
        if el["type"] == "bullet" and el.get("origin") == "user" and not include_user_edits:
            continue  # the user's own edits are preserved unless explicitly included
        sec = el.get("section")
        if whole or any(s["id"] == sec for s in secs):
            out.append((eid, el))
    return out


REWRITE_SYSTEM = (
    "You edit individual resume items. Apply the user's request to each item you are given and return every item. "
    "Keep every fact identical: do not add technologies, numbers, users, scale, impact, leadership or awards that "
    "the item does not state. Past tense, no first person, no filler words (leveraged, cutting-edge, spearheaded, "
    "robust, innovative, seamless, utilized, passionate). Keep the author's own phrasing where it is already clear. "
    'Return JSON: {"items": {"<id>": "<new text>"}}'
)


def _rewrite(
    content: dict, text: str, selection: list[str], provider: LLMProvider | None, focus: list[str], p: Plan
) -> Plan:
    low = text.lower()
    include_user = bool(re.search(r"including (?:my|the) edits|my edits too|everything including", low)) or (
        bool(selection) and not _sections_named(content, low)
    )
    targets = _targets(content, low, selection, include_user)
    if not targets:
        p.message = "Select the text to change, or name the section (summary, projects, experience)."
        return p
    if provider is None:
        return _deterministic_rewrite(targets, low, p)
    items = {eid: el["text"] for eid, el in targets}
    prompt = f"Request: {text}\nSkills the job cares about: {', '.join(focus[:12])}\n\n" + wrap_untrusted(
        "ITEMS", json.dumps(items, ensure_ascii=False), 12000
    )
    try:
        data = provider.complete_json(REWRITE_SYSTEM, prompt, max_tokens=3000, temperature=0.3)
    except LLMError as exc:
        p.message = f"The writing assistant is unavailable ({exc})."
        return p
    new = data.get("items") or {}
    for eid, el in targets:
        cand = new.get(eid)
        if not isinstance(cand, str) or not cand.strip() or cand.strip() == el["text"]:
            continue
        cand = re.sub(r"\s+", " ", cand).strip()
        source = el["text"]
        verdict = check_rewrite(cand, source, allowed_skills_for([source], {m.key for m in find_skills(source)}))
        if not verdict.ok:
            p.refused.append(f"Kept “{el['text'][:60]}…”: the rewrite {verdict.problems[0]}.")
            continue
        p.ops.append({"op": "update_text", "id": eid, "text": cand})
    p.summary.append(f"Rewrite {len(p.ops)} item{'s' if len(p.ops) != 1 else ''}")
    p.broad = len(p.ops) > 3
    if not p.ops:
        p.message = p.refused[0] if p.refused else "No change was needed."
    return p


def _deterministic_rewrite(targets: list[tuple[str, dict]], low: str, p: Plan) -> Plan:
    """Without a model, only removals are safe: shorten by dropping trailing clauses or sentences, never adding."""
    if not re.search(r"shorten|shorter|concise|trim|tighten|two lines|2 lines|one line|1 line|brief", low):
        p.message = (
            "Rewording needs an AI provider (Settings → AI provider). Without one I can shorten text, move, "
            "remove and restore content, and change links and sections."
        )
        return p
    for eid, el in targets:
        t = el["text"]
        if el["type"] == "paragraph":
            sentences = re.split(r"(?<=[.!?])\s+", t)
            keep = 1 if re.search(r"one line|1 line", low) else 2
            new = " ".join(sentences[:keep])
            if new == t and keep == 1 and len(t.split()) > 14:
                cut = max(t.rfind(" with "), t.rfind(", "), t.rfind(" using "))
                if cut > 30:
                    new = t[:cut].rstrip(",") + "."
        else:
            words = t.split()
            if len(words) <= 18:
                continue
            clipped = " ".join(words[:22])
            cut = max(clipped.rfind(", "), clipped.rfind(" and "), clipped.rfind(" with "))
            new = (clipped[:cut] if cut > 40 else clipped).rstrip(",;") + "."
        if new and new != t:
            p.ops.append({"op": "update_text", "id": eid, "text": new})
    p.summary.append(f"Shorten {len(p.ops)} item{'s' if len(p.ops) != 1 else ''} (removing, never adding)")
    p.broad = len(p.ops) > 3
    if not p.ops:
        p.message = "Those items are already short."
    return p


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
