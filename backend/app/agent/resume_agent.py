"""The resume agent: natural-language requests become verified, targeted edits of the current resume.

    understand  ->  inspect state  ->  plan  ->  execute  ->  verify  ->  report (or ask one question)

Understanding is layered. Clear requests ("put Resolve first", "remove portfolio", "one page") are resolved by
rules, with no model call. Anything the rules cannot place is resolved by the configured model against an
outline of the current document (ids, sections, entries, links, selection, last edited target), and every step
it proposes is validated against that outline. Only if neither can place a request does the agent ask - one
short question with concrete choices, never a list of example commands.

Execution edits only the targeted elements. Text changes (expand, shorten, rewrite, emphasis) draw only on the
element itself plus verified facts from the career profile and repository evidence, and every result passes the
truth check before it is applied. After applying, the resume is re-rendered, fitted to the page target if needed,
and checked (page count, evidence, stuffing, layout, ATS extraction) with deterministic checks only.
"""

import json
import math
import re
from dataclasses import dataclass, field

from app.llm.provider import LLMError, LLMProvider, wrap_untrusted
from app.matching.taxonomy import display_name, find_skills
from app.resume.assistant import LINK_WORDS, _match_entry, _norm, _sections_named
from app.resume.document import index
from app.resume.tailor import plain_verbs
from app.resume.truth import allowed_skills_for, check_rewrite

TEXT_OPS = ("EXPAND_TEXT", "SHORTEN_TEXT", "RESIZE_TEXT", "REWRITE_TEXT", "CHANGE_EMPHASIS", "UPDATE_TEXT")
STRUCT_OPS = (
    "ADD_LINK",
    "REMOVE_LINK",
    "REORDER_ENTRY",
    "REMOVE_SECTION",
    "RESTORE_SECTION",
    "REMOVE_ENTRY",
    "RESTORE_ENTRY",
    "REMOVE_SKILL",
    "ADD_SKILL",
    "REDUCE_CONTENT",
    "LIMIT_ENTRIES",
)
DOC_OPS = ("SET_PAGE_TARGET", "UNDO", "REDO", "RESTORE_VERSION")
ALL_OPS = TEXT_OPS + STRUCT_OPS + DOC_OPS
# structural changes first, then wording, then page fitting last (it depends on everything before it)
ORDER = {**dict.fromkeys(STRUCT_OPS, 0), **dict.fromkeys(TEXT_OPS, 1), "SET_PAGE_TARGET": 2}

VERB = (
    r"make|put|move|place|remove|drop|delete|hide|add|include|keep|shorten|trim|tighten|expand|increase|lengthen|"
    r"extend|elaborate|rewrite|reword|rephrase|improve|strengthen|polish|fit|set|restore|bring|show|exclude|focus|"
    r"emphasi[sz]e|highlight|use|cut|reduce|swap|change|undo|redo|get rid"
)
EXPAND_RX = re.compile(
    r"\b(increase|expand|longer|lengthen|extend|elaborate|more detail|add more|grow|bigger|some more|a bit more)\b"
)
SHORTEN_RX = re.compile(
    r"\b(shorten|shorter|trim|tighten|concise|cut down|reduce|brief|condense|smaller|less wordy|too long)\b"
)
REWRITE_RX = re.compile(
    r"\b(natural|generic|sound(?:s)? like me|like me|human|rewrite|reword|rephrase|improve|stronger|strengthen|"
    r"better|polish|clearer|simplif|less robotic|flow|punchier|more specific)\w*"
)
EMPHASIS_RX = re.compile(
    r"\b(focus|emphasi[sz]e|highlight|use (?:the |my )?(.+?) (?:experience|work|skills) more|lean)\w*"
)
PRONOUN_RX = re.compile(r"\b(this|it|that|these|those|selected|selection|the highlighted)\b")
LINES_RX = re.compile(r"\b(?:about|around|roughly|~)?\s*(one|two|three|four|five|six|\d)\s+lines?\b")
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
ORDINAL = {"first": 0, "top": 0, "second": 1, "third": 2, "fourth": 3, "last": -1, "bottom": -1}


@dataclass
class Step:
    op: str
    target: str = ""  # element, section or entry id, or a link kind
    params: dict = field(default_factory=dict)
    clause: str = ""
    label: str = ""


@dataclass
class Understanding:
    steps: list[Step] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    question: str = ""
    options: list[str] = field(default_factory=list)
    used_model: bool = False
    notes: list[str] = field(default_factory=list)  # parts of the request that could not be grounded


@dataclass
class Context:
    content: dict
    selection: list[str]
    last_targets: list[str]
    page_target: int
    page_count: int | None
    focus: list[str]  # JD skills, most important first
    facts: list[str]  # verified statements the agent may draw on
    evidence_keys: set[str]
    style_samples: list[str]
    chars_per_line: int = 110


# --- understanding ----------------------------------------------------------------------------------


def split_clauses(text: str) -> list[str]:
    """Splits a multi-part request at boundaries that start a new instruction."""
    t = re.sub(r"\s+", " ", text.strip().rstrip("."))
    parts = re.split(
        rf"\s*(?:;|\.\s+|,?\s+then\s+|,?\s+and then\s+|,\s*(?:and\s+)?(?=(?:{VERB})\b)|\s+and\s+(?=(?:{VERB})\b)|"
        rf"\s+but\s+)\s*",
        t,
        flags=re.I,
    )
    out: list[str] = []
    for p in parts:
        p = p.strip(" ,")
        if not p:
            continue
        # "put Resolve first and EventSphere second": the second half inherits the verb
        sub = re.split(r"\s+and\s+(?=[\w .-]+?\s+(?:first|second|third|fourth|last)\b)", p, flags=re.I)
        out.extend(s.strip() for s in sub if s.strip())
    return out


STOP = {
    "the",
    "and",
    "for",
    "with",
    "project",
    "projects",
    "platform",
    "system",
    "app",
    "application",
    "first",
    "second",
    "third",
    "last",
    "put",
    "move",
    "make",
    "remove",
    "this",
    "that",
    "my",
    "top",
}


def _name_words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in STOP}


def _entry_in(content: dict, low: str) -> tuple[dict, dict] | None:
    """An entry the request names: full or short name, or a distinctive word of its name ("Resolve")."""
    hit = _match_entry(content, low)
    if hit:
        return hit
    words = set(re.findall(r"[a-z0-9]+", low))
    best, score = None, 0
    for s in content["sections"]:
        if s.get("removed"):
            continue
        for e in s.get("entries") or []:
            name = e["fields"].get("short_name") or e["fields"].get("primary", "")
            common = _name_words(name) & words
            if common and len(common) > score:
                best, score = (s, e), len(common)
    return best


def _sections_in(content: dict, low: str) -> list[dict]:
    """Sections named in a request: by kind, exact title, or a distinctive title word ("certifications")."""
    found = _sections_named(content, low)
    words = set(re.findall(r"[a-z]+", low))
    for s in content["sections"]:
        if s in found:
            continue
        title_words = {w for w in re.findall(r"[a-z]+", (s.get("title_text") or s["title"]).lower()) if len(w) >= 5}
        if any(w in words or w.rstrip("s") in words or w + "s" in words for w in title_words):
            found.append(s)
    return found


def _section(content: dict, kind: str) -> dict | None:
    return next((s for s in content["sections"] if s["kind"] == kind and not s.get("removed")), None)


def _link_kind(low: str) -> str | None:
    for kind, words in LINK_WORDS.items():
        if re.search(rf"\b(?:{words})\b", low):
            return kind
    return None


def _target_for(ctx: Context, low: str) -> tuple[str, str]:
    """(kind, id) of what a clause is about: a selection, a named section or entry, the last target, or ''."""
    elements = index(ctx.content)
    secs = _sections_in(ctx.content, low)
    bullet_word = re.search(r"\b(bullet|point|line item)s?\b", low)
    section_word = re.search(r"\b(this|that|the|current) section\b", low)
    if section_word and ctx.selection and not secs:
        sec = elements.get(ctx.selection[0], {}).get("section")
        if sec:
            return "section", sec
    if PRONOUN_RX.search(low) and ctx.selection:
        return "selection", ",".join(ctx.selection)
    if bullet_word and not ctx.selection:
        ent = _entry_in(ctx.content, low)
        if ent:
            return "entry", ent[1]["id"]
        bullets = [t for t in ctx.last_targets if elements.get(t, {}).get("type") == "bullet"]
        return ("last", ",".join(bullets)) if bullets else ("", "")
    ent = _entry_in(ctx.content, low)
    if ent and not (secs and ent[0]["id"] != secs[0]["id"]):
        return "entry", ent[1]["id"]
    if secs:
        return "section", secs[0]["id"]
    if re.search(r"\b(whole|entire|everything|all of it|resume|overall)\b", low):
        return "resume", ""
    if ctx.selection:
        return "selection", ",".join(ctx.selection)
    live = [t for t in ctx.last_targets if t in elements or any(s["id"] == t for s in ctx.content["sections"])]
    if live:
        return "last", ",".join(live)
    return "", ""


def _lines_wanted(low: str) -> int | None:
    m = LINES_RX.search(low)
    if not m:
        return None
    v = m.group(1)
    return WORDNUM.get(v) or int(v)


def rule_step(ctx: Context, clause: str) -> Step | None:
    """One clause -> one step, when the rules can place it with confidence. None when they cannot."""
    low = clause.lower().strip()
    content = ctx.content

    if re.fullmatch(r"(undo|undo (?:that|it|the last change)|revert (?:that|it))", low):
        return Step("UNDO", clause=clause, label="Undo the last change")
    if re.fullmatch(r"redo(?: that| it)?", low):
        return Step("REDO", clause=clause, label="Redo")
    m = re.search(r"\b(?:restore|go back to|revert to)\s+(?:revision|version)\s+(\d+)", low)
    if m:
        return Step("RESTORE_VERSION", params={"number": int(m.group(1))}, clause=clause)

    m = re.search(r"\b(one|1|two|2)[\s-]pages?\b|\bsingle page\b", low)
    if m and not EXPAND_RX.search(low) and not REWRITE_RX.search(low):
        pages = 1 if (not m.group(1) or m.group(1) in ("one", "1")) else 2
        return Step("SET_PAGE_TARGET", params={"pages": pages}, clause=clause, label=f"Fit to {pages} page(s)")

    kind = _link_kind(low)
    if kind and not re.search(r"\bprojects?\b", low):
        if re.search(r"\b(remove|hide|drop|delete|exclude|without|no|don'?t|out|take off|get rid)\b", low):
            return Step("REMOVE_LINK", target=kind, clause=clause, label=f"Remove the {kind} link")
        if re.search(r"\b(add|include|show|keep|put|restore|bring back|with)\b", low):
            return Step("ADD_LINK", target=kind, clause=clause, label=f"Show the {kind} link")

    m = re.search(r"\b(add|remove|drop)\b\s+(.+?)\s+(?:to|from|in)\s+(?:the\s+|my\s+)?skills\b", low)
    if m:
        op = "ADD_SKILL" if m.group(1) == "add" else "REMOVE_SKILL"
        return Step(op, target=m.group(2).strip(), clause=clause, label=f"{m.group(1).title()} {m.group(2)}")

    if re.search(
        r"\b(isn'?t|not|doesn'?t|don'?t)\b.*\b(help|relevant|matter|useful)|\birrelevant\b|\bunrelated\b", low
    ):
        return Step("REDUCE_CONTENT", clause=clause, label="Remove content that does not support this job")

    m = re.search(
        r"\b(?:use|keep|show|include|limit(?: it)? to|only)\s+(?:only\s+)?(?:the\s+)?(?:top\s+|best\s+|first\s+)?"
        r"(one|two|three|four|five|six|\d)\s+(projects?|experiences?|roles?|jobs?|certifications?)\b",
        low,
    )
    if m:
        n = WORDNUM.get(m.group(1)) or int(m.group(1))
        kind = {
            "project": "projects",
            "experience": "experience",
            "role": "experience",
            "job": "experience",
            "certification": "certifications",
        }[m.group(2).rstrip("s")]
        sec = next((x for x in content["sections"] if x["kind"] == kind and not x.get("removed")), None)
        if sec:
            return Step(
                "LIMIT_ENTRIES",
                target=sec["id"],
                params={"n": n},
                clause=clause,
                label=f"Keep {n} {m.group(2) if n != 1 else m.group(2).rstrip('s')}",
            )

    ordinal = next((v for k, v in ORDINAL.items() if re.search(rf"\b{k}\b", low)), None)
    rel = re.search(r"\b(above|before|below|after)\b(.+)$", low)
    if ordinal is not None or rel:
        ent = _entry_in(content, re.split(r"\b(above|before|below|after)\b", low)[0])
        if ent:
            sec, e = ent
            entries = [x for x in sec["entries"]]
            if ordinal is not None:
                pos = ordinal if ordinal >= 0 else len(entries) - 1
            else:
                other = _entry_in(content, rel.group(2))
                if not other or other[1] is e:
                    return None
                rest = [x["id"] for x in entries if x is not e]
                j = rest.index(other[1]["id"])
                pos = j if rel.group(1) in ("above", "before") else j + 1
            name = e["fields"].get("short_name") or e["fields"].get("primary")
            return Step(
                "REORDER_ENTRY",
                target=e["id"],
                params={"position": pos},
                clause=clause,
                label=f"Move {name} to position {pos + 1}",
            )

    if re.search(r"\b(restore|bring back|add back|put back|re-?add)\b", low):
        for s in content["sections"]:
            if s.get("removed") and s in _sections_in(content, low):
                return Step("RESTORE_SECTION", target=s["id"], clause=clause, label=f"Restore {s['title']}")
        for s in content["sections"]:
            for e in s.get("removed_entries") or []:
                if _norm(e["fields"].get("short_name", "") or e["fields"].get("primary", "")) in _norm(low):
                    return Step(
                        "RESTORE_ENTRY", target=e["id"], clause=clause, label=f"Restore {e['fields'].get('primary')}"
                    )

    text_op = None
    if EXPAND_RX.search(low) or (
        re.search(r"\bmore\b", low)
        and not SHORTEN_RX.search(low)
        and not REWRITE_RX.search(low)
        and not EMPHASIS_RX.search(low)
    ):
        text_op = "EXPAND_TEXT"
    elif SHORTEN_RX.search(low):
        text_op = "SHORTEN_TEXT"
    elif EMPHASIS_RX.search(low):
        text_op = "CHANGE_EMPHASIS"
    elif REWRITE_RX.search(low):
        text_op = "REWRITE_TEXT"
    lines = _lines_wanted(low)
    if lines and not text_op:
        text_op = "RESIZE_TEXT"

    if re.search(r"\b(remove|delete|drop|hide|get rid of|take out|exclude)\b", low) and not text_op:
        ent = _entry_in(content, low)
        secs = _sections_in(content, low)
        says_section = re.search(r"\b(section|all (?:the |my )?(?:projects|experience|certifications))\b", low)
        if ent and (not secs or (ent[0] in secs and not says_section)):
            return Step(
                "REMOVE_ENTRY", target=ent[1]["id"], clause=clause, label=f"Remove {ent[1]['fields'].get('primary')}"
            )
        if secs:
            return Step(
                "REMOVE_SECTION",
                target=secs[0]["id"],
                clause=clause,
                label=f"Remove the {secs[0].get('title_text') or secs[0]['title']} section",
            )
        return None

    if text_op:
        tkind, tid = _target_for(ctx, low)
        if not tkind:
            return None
        focus = ""
        m = re.search(
            r"\b(?:focus(?:ing)?|emphasi[sz]\w*|highlight\w*|lean)\s+(?:more\s+)?(?:on\s+)?(?:my\s+|the\s+)?(.+)$", low
        )
        if m:
            focus = m.group(1).strip()
        m2 = re.search(r"\buse (?:the |my )?(.+?) (?:experience|work|skills) more\b", low)
        if m2:
            focus = m2.group(1)
        return Step(
            text_op, target=f"{tkind}:{tid}", params={"lines": lines, "focus": focus, "request": clause}, clause=clause
        )
    return None


def understand(ctx: Context, instruction: str, provider: LLMProvider | None) -> Understanding:
    u = Understanding()
    for clause in split_clauses(instruction):
        step = rule_step(ctx, clause)
        prev = u.steps[-1] if u.steps else None
        if step is None and prev is not None and prev.op in TEXT_OPS:
            # "make the summary 4 lines and focus on backend": the second part is about the same target
            kind, _, tid = prev.target.partition(":")
            follow = rule_step(ra_ctx_with_last(ctx, tid.split(",") if tid else []), clause)
            if follow is not None and follow.op in TEXT_OPS:
                prev.params["focus"] = follow.params.get("focus") or prev.params.get("focus", "")
                prev.params["lines"] = prev.params.get("lines") or follow.params.get("lines")
                if follow.op in ("CHANGE_EMPHASIS", "REWRITE_TEXT") and prev.op == "RESIZE_TEXT":
                    prev.op = follow.op
                prev.params["request"] = prev.params.get("request", "") + "; " + clause
                continue
        if step is None:
            u.unresolved.append(clause)
        else:
            u.steps.append(step)
    if u.unresolved and provider is not None:
        model_steps, question, dropped = _model_understand(ctx, instruction, provider)
        if dropped:
            known = json.dumps(outline(ctx)).lower()
            names = [w for w in re.findall(r"\b[A-Z][A-Za-z0-9]+\b", instruction) if w.lower() not in known]
            what = ", ".join(dict.fromkeys(names)) or "part of the request"
            u.notes.append(f"Couldn't find {what} on this resume, so nothing was moved or removed for it")
        if model_steps is not None:
            u.used_model = True
            if model_steps:
                u.steps, u.unresolved = model_steps, []
            elif question:
                u.question = question
    if u.unresolved and not u.question:
        u.question, u.options = _question_for(ctx, u.unresolved[0])
    return u


def ra_ctx_with_last(ctx: Context, ids: list[str]) -> Context:
    return Context(**{**ctx.__dict__, "last_targets": ids or ctx.last_targets, "selection": []})


def _question_for(ctx: Context, clause: str) -> tuple[str, list[str]]:
    low = clause.lower()
    if re.search(r"\b(bullet|point)s?\b", low):
        return "Which bullet? Click it in the editor, then ask again — or name the project or role it belongs to.", []
    names = [s.get("title_text") or s["title"] for s in ctx.content["sections"] if not s.get("removed")]
    if EXPAND_RX.search(low) or SHORTEN_RX.search(low) or REWRITE_RX.search(low) or EMPHASIS_RX.search(low):
        return "Which part should I change?", [n for n in names if n][:5]
    return f"I'm not sure what “{clause}” should change. Which section is it about?", [n for n in names if n][:5]


INTENT_SYSTEM = (
    "You translate a request about the user's resume into edit steps on the CURRENT document, whose outline is "
    "given. Never regenerate the resume. Use only ids from the outline. Operations:\n"
    "EXPAND_TEXT / SHORTEN_TEXT / REWRITE_TEXT / CHANGE_EMPHASIS: target = 'element:<id>' (paragraph or bullet), "
    "'section:<section id>', 'entry:<entry id>', 'selection:' or 'resume:'; params: {lines: int|null, "
    "focus: str, request: str}\n"
    "ADD_LINK / REMOVE_LINK: target = portfolio|github|linkedin\n"
    "REORDER_ENTRY: target = entry id; params {position: 0-based}\n"
    "REMOVE_SECTION / RESTORE_SECTION: target = section id. REMOVE_ENTRY / RESTORE_ENTRY: target = entry id\n"
    "REMOVE_SKILL / ADD_SKILL: target = skill name. REDUCE_CONTENT: remove content least relevant to the job\n"
    "LIMIT_ENTRIES: target = section id; params {n: how many entries to keep}\n"
    "SET_PAGE_TARGET: params {pages: 1|2}. UNDO. REDO.\n"
    "Pronouns (this, it, that) refer to the selection, else to the last edited target. "
    'Return {"steps": [{"op": "...", "target": "...", "params": {}, "label": "short description"}], '
    '"question": null} - or steps [] and a single short question only if the request cannot reasonably be placed.'
)


def outline(ctx: Context) -> dict:
    els = index(ctx.content)
    secs = []
    for s in ctx.content["sections"]:
        item = {
            "id": s["id"],
            "kind": s["kind"],
            "title": s.get("title_text") or s["title"],
            "removed": bool(s.get("removed")),
        }
        if s.get("layout") == "paragraph":
            item["text_id"] = s["text_id"]
            item["text"] = s.get("text", "")[:300]
        if s.get("entries"):
            item["entries"] = [
                {
                    "id": e["id"],
                    "name": e["fields"].get("short_name") or e["fields"].get("primary", ""),
                    "bullets": [b["id"] for b in e.get("bullets") or [] if not b.get("tech_line")],
                }
                for e in s["entries"]
            ]
        if s.get("removed_entries"):
            item["removed_entries"] = [
                {"id": e["id"], "name": e["fields"].get("primary", "")} for e in s["removed_entries"]
            ]
        secs.append(item)
    return {
        "sections": secs,
        "links": [{"kind": lk["kind"], "mode": lk["mode"]} for lk in ctx.content.get("header", {}).get("links", [])],
        "selection": [{"id": i, "text": els.get(i, {}).get("text", "")[:200]} for i in ctx.selection],
        "last_edited": ctx.last_targets[:5],
        "page_target": ctx.page_target,
        "pages": ctx.page_count,
    }


def _grounded(ctx: Context, entry_id: str, instruction: str) -> bool:
    """A model may only move or remove an entry the user actually referred to (by name, position or selection)."""
    low = instruction.lower()
    entry = next(
        (
            e
            for s in ctx.content["sections"]
            for e in (s.get("entries") or []) + (s.get("removed_entries") or [])
            if e["id"] == entry_id
        ),
        None,
    )
    if entry is None:
        return False
    name = f"{entry['fields'].get('short_name', '')} {entry['fields'].get('primary', '')}"
    if _name_words(name) & set(re.findall(r"[a-z0-9]+", low)):
        return True
    if re.search(
        r"\bthe (first|second|third|last|top|bottom|latest|oldest) (project|entry|experience|job|role|one|item)", low
    ):
        return True
    els = index(ctx.content)
    return any(els.get(i, {}).get("entry") == entry_id or i == entry_id for i in ctx.selection + ctx.last_targets)


def _model_understand(
    ctx: Context, instruction: str, provider: LLMProvider
) -> tuple[list[Step] | None, str, list[str]]:
    prompt = f"Document outline:\n{json.dumps(outline(ctx), ensure_ascii=False)}\n\nRequest: {instruction}"
    try:
        data = provider.complete_json(INTENT_SYSTEM, prompt, max_tokens=1200, temperature=0.1)
    except LLMError:
        return None, "", []
    dropped: list[str] = []
    els = index(ctx.content)
    section_ids = {s["id"] for s in ctx.content["sections"]}
    entry_ids = {
        e["id"] for s in ctx.content["sections"] for e in (s.get("entries") or []) + (s.get("removed_entries") or [])
    }
    steps: list[Step] = []
    for raw in data.get("steps") or []:
        if not isinstance(raw, dict) or raw.get("op") not in ALL_OPS:
            continue
        op, target, params = raw["op"], str(raw.get("target") or ""), raw.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if op in TEXT_OPS:
            kind, _, tid = target.partition(":")
            if kind == "element" and tid not in els:
                continue
            if kind == "section" and tid not in section_ids:
                continue
            if kind == "entry" and tid not in entry_ids:
                continue
            if kind == "selection":
                tid = ",".join(ctx.selection)
            if kind not in ("element", "section", "entry", "selection", "resume"):
                continue
            params = {
                "lines": params.get("lines") if isinstance(params.get("lines"), int) else None,
                "focus": str(params.get("focus") or "")[:120],
                "request": instruction,
            }
            target = f"{kind}:{tid}"
        elif (
            op in ("ADD_LINK", "REMOVE_LINK")
            and target not in LINK_WORDS
            or op in ("REORDER_ENTRY", "REMOVE_ENTRY", "RESTORE_ENTRY")
            and target not in entry_ids
            or op in ("REMOVE_SECTION", "RESTORE_SECTION")
            and target not in section_ids
        ):
            continue
        elif op in ("REORDER_ENTRY", "REMOVE_ENTRY", "RESTORE_ENTRY") and not _grounded(ctx, target, instruction):
            dropped.append(str(raw.get("label") or op))
            continue
        elif op == "LIMIT_ENTRIES":
            if target not in section_ids or not str(params.get("n", "")).isdigit():
                continue
            params = {"n": max(1, int(params["n"]))}
        elif op == "SET_PAGE_TARGET":
            params = {"pages": 2 if params.get("pages") == 2 else 1}
        elif op == "REORDER_ENTRY":
            params = {"position": int(params.get("position") or 0)}
        steps.append(Step(op, target, params, instruction, str(raw.get("label") or "")[:120]))
    q = data.get("question")
    return steps, (str(q)[:200] if q and not steps else ""), dropped


# --- execution ----------------------------------------------------------------------------------------


def resolve_text_targets(ctx: Context, target: str, include_user: bool) -> list[tuple[str, dict]]:
    els = index(ctx.content)
    kind, _, tid = target.partition(":")
    ids: list[str] = []
    if kind in ("selection", "last", "element"):
        ids = [i for i in tid.split(",") if i]
        expanded = []
        for i in ids:
            sec = next((s for s in ctx.content["sections"] if s["id"] == i), None)
            if sec is not None:  # the last target can be a whole section
                expanded += [
                    x for x, el in els.items() if el.get("section") == i and el["type"] in ("paragraph", "bullet")
                ]
            elif els.get(i, {}).get("type") == "entry":
                expanded += [x for x, el in els.items() if el.get("entry") == i and el["type"] == "bullet"]
            else:
                expanded.append(i)
        ids = expanded
        include_user = True
    elif kind == "section":
        ids = [x for x, el in els.items() if el.get("section") == tid and el["type"] in ("paragraph", "bullet")]
    elif kind == "entry":
        ids = [x for x, el in els.items() if el.get("entry") == tid and el["type"] == "bullet"]
    elif kind == "resume":
        ids = [x for x, el in els.items() if el["type"] in ("paragraph", "bullet")]
    out = []
    for i in ids:
        el = els.get(i)
        if not el or el["type"] not in ("paragraph", "bullet", "item") or el.get("tech_line"):
            continue
        if el.get("origin") == "user" and not include_user:
            continue
        out.append((i, el))
    return out


def _lines_of(ctx: Context, text: str) -> int:
    return max(1, math.ceil(len(text) / max(40, ctx.chars_per_line)))


REWRITE_SYSTEM = (
    "You edit individual resume items in place. For each item apply the request and return every item. "
    "Use only facts stated in the item itself or in FACTS (the candidate's verified profile and repository "
    "evidence). Never add technologies, numbers, users, scale, impact, leadership or awards not stated there. "
    "Resume style: past tense for bullets, no first person, no filler words (leveraged, cutting-edge, spearheaded, "
    "robust, innovative, seamless, utilized, passionate, dynamic, synergy). Write like a careful engineer, not "
    "marketing. When a target length is given as characters, stay within 10% of it. "
    'Return JSON: {"items": {"<id>": "<new text>"}}'
)


def _text_instruction(step: Step, ctx: Context, el: dict) -> str:
    p = step.params
    cur_lines = _lines_of(ctx, el["text"])
    want = p.get("lines")
    if step.op == "EXPAND_TEXT":
        want = want or cur_lines + (1 if cur_lines >= 3 else max(1, cur_lines // 2 + 1))
        instr = f"Expand this item to about {want} lines (~{want * ctx.chars_per_line} characters) using FACTS."
    elif step.op == "SHORTEN_TEXT":
        want = want or max(1, cur_lines - 1)
        instr = f"Shorten this item to about {want} line(s) (~{int(want * ctx.chars_per_line * 0.95)} characters)."
    elif step.op == "RESIZE_TEXT":
        instr = f"Make this item about {want} lines (~{want * ctx.chars_per_line} characters), using FACTS if longer."
    elif step.op == "CHANGE_EMPHASIS":
        instr = f"Rewrite to foreground {p.get('focus') or 'the skills this job needs'} where the facts support it."
        if want:
            instr += f" About {want} lines (~{want * ctx.chars_per_line} characters)."
    else:
        instr = f"Rewrite this item: {p.get('request') or step.clause}."
        if want:
            instr += f" About {want} lines (~{want * ctx.chars_per_line} characters)."
    if p.get("focus") and step.op != "CHANGE_EMPHASIS":
        instr += f" Emphasise {p['focus']} where the facts support it."
    if re.search(r"like me|my voice|my style|sound(?:s)? like me", (p.get("request") or "").lower()):
        instr += " Match the author's own writing style shown in STYLE."
    return instr


def plan_text_edits(ctx: Context, step: Step, provider: LLMProvider | None) -> tuple[list[dict], list[str], str]:
    """Returns (update ops, notes, failure message)."""
    include_user = bool(re.search(r"including (?:my|the) edits|my edits too", step.clause.lower()))
    targets = resolve_text_targets(ctx, step.target, include_user)
    if not targets:
        return [], [], "nothing editable in that part of the resume"
    if provider is None:
        return _deterministic_text(ctx, step, targets)
    allowed = allowed_skills_for(ctx.facts, ctx.evidence_keys)
    fact_text = " ".join(ctx.facts)
    job_skills = [display_name(k) for k in ctx.focus if k in allowed][:12]
    pending = dict(targets)
    ops: list[dict] = []
    notes: list[str] = []
    feedback: dict[str, str] = {}
    problems: dict[str, str] = {}
    for attempt in range(2):  # one retry, told exactly why the first attempt was rejected
        if not pending:
            break
        items = {eid: el["text"] for eid, el in pending.items()}
        instructions = "\n".join(
            f"- {eid}: {_text_instruction(step, ctx, el)}"
            + (
                f" A previous attempt was rejected because it {feedback[eid]}; do not do that."
                if eid in feedback
                else ""
            )
            for eid, el in pending.items()
        )
        prompt = (
            f"Instructions per item id:\n{instructions}\n"
            f"Job skills the candidate has evidence for: {', '.join(job_skills) or 'none listed'}\n\n"
            + wrap_untrusted("FACTS", "\n".join(f"- {f}" for f in ctx.facts[:70]), 9000)
            + "\n"
            + wrap_untrusted("STYLE", "\n".join(ctx.style_samples[:6]), 2000)
            + "\n"
            + wrap_untrusted("ITEMS", json.dumps(items, ensure_ascii=False), 12000)
            + '\nReturn {"items": {"<item id>": "<new text>"}} using exactly the item ids above.'
        )
        try:
            data = provider.complete_json(REWRITE_SYSTEM, prompt, max_tokens=3500, temperature=0.3)
        except LLMError as exc:
            if step.op == "SHORTEN_TEXT" and attempt == 0:
                return _deterministic_text(ctx, step, targets)
            return ops, notes, str(exc)
        new = _find_items(data, set(pending))
        rejected: dict[str, str] = {}
        for eid, el in list(pending.items()):
            cand = new.get(eid)
            if not isinstance(cand, str) or not cand.strip():
                continue
            cand = re.sub(r"\s+", " ", cand).strip()
            if el["type"] == "bullet" and cand.split(" ", 1)[0] != el["text"].split(" ", 1)[0]:
                cand = plain_verbs(cand)  # a verb the model chose, not one the user already had
            if cand == el["text"]:
                pending.pop(eid)
                continue
            uses_facts = step.op in ("EXPAND_TEXT", "RESIZE_TEXT", "CHANGE_EMPHASIS", "REWRITE_TEXT")
            source = el["text"] + (" " + fact_text if uses_facts else "")
            max_len = 900 if el["type"] == "paragraph" else 400
            verdict = check_rewrite(cand, source, allowed | {m.key for m in find_skills(el["text"])}, max_len)
            if verdict.ok:
                ops.append({"op": "update_text", "id": eid, "text": cand})
                pending.pop(eid)
                problems.pop(eid, None)
            else:
                rejected[eid] = problems[eid] = verdict.problems[0]
        feedback = rejected
        pending = {eid: el for eid, el in pending.items() if eid in rejected}
    for eid, problem in problems.items():
        notes.append(f"Kept “{dict(targets)[eid]['text'][:50]}…” unchanged: the rewrite {problem}.")
    return ops, notes, ""


def _find_items(data, ids: set[str]) -> dict[str, str]:
    """Item texts keyed by id, wherever a model nested them in its JSON."""
    found: dict[str, str] = {}

    def walk(node, depth=0):
        if depth > 5:
            return
        if isinstance(node, dict):
            for k, val in node.items():
                if k in ids and isinstance(val, str):
                    found.setdefault(k, val)
                elif k in ids and isinstance(val, dict) and isinstance(val.get("text"), str):
                    found.setdefault(k, val["text"])
                else:
                    walk(val, depth + 1)
        elif isinstance(node, list):
            for x in node:
                walk(x, depth + 1)

    walk(data)
    return found


def _deterministic_text(ctx: Context, step: Step, targets) -> tuple[list[dict], list[str], str]:
    """Without a model: shortening by removing clauses, expanding a summary only from verified facts."""
    ops: list[dict] = []
    if step.op == "SHORTEN_TEXT" or (
        step.op == "RESIZE_TEXT"
        and all(_lines_of(ctx, el["text"]) > (step.params.get("lines") or 1) for _, el in targets)
    ):
        want = step.params.get("lines")
        for eid, el in targets:
            t = el["text"]
            limit = int((want or max(1, _lines_of(ctx, t) - 1)) * ctx.chars_per_line * 0.95)
            new = _clip(t, limit)
            if new != t:
                ops.append({"op": "update_text", "id": eid, "text": new})
        return ops, [], "" if ops else "it is already that short"
    if step.op in ("EXPAND_TEXT", "RESIZE_TEXT"):
        for eid, el in targets:
            if el["type"] != "paragraph":
                continue
            add = _fact_sentence(ctx, el["text"])
            if add:
                ops.append({"op": "update_text", "id": eid, "text": el["text"].rstrip() + " " + add})
        if ops:
            return ops, ["Expanded from verified profile skills and projects (no AI provider configured)."], ""
    return [], [], "rewording needs an AI provider (Settings → AI providers)"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > limit and out:
            break
        out = (out + " " + s).strip()
    if len(out) > limit:
        clipped = out[:limit]
        cut = max(clipped.rfind(", "), clipped.rfind(" and "), clipped.rfind(" with "), clipped.rfind(" using "))
        out = (clipped[:cut] if cut > limit * 0.5 else clipped.rsplit(" ", 1)[0]).rstrip(",;") + "."
    return out


def _fact_sentence(ctx: Context, text: str) -> str:
    low = text.lower()
    skills = [display_name(k) for k in ctx.focus if k in ctx.evidence_keys and display_name(k).lower() not in low][:4]
    if not skills:
        return ""
    return (
        "Hands-on experience with "
        + (", ".join(skills[:-1]) + " and " + skills[-1] if len(skills) > 1 else skills[0])
        + "."
    )


def structural_ops(ctx: Context, step: Step) -> tuple[list[dict], str]:
    c = ctx.content
    if step.op in ("ADD_LINK", "REMOVE_LINK"):
        return [{"op": "set_link", "kind": step.target, "mode": "include" if step.op == "ADD_LINK" else "exclude"}], ""
    if step.op == "REORDER_ENTRY":
        return [{"op": "move_entry", "id": step.target, "position": step.params.get("position", 0)}], ""
    if step.op == "REMOVE_SECTION":
        return [{"op": "remove_section", "id": step.target}], ""
    if step.op == "RESTORE_SECTION":
        return [{"op": "restore_section", "id": step.target}], ""
    if step.op == "REMOVE_ENTRY":
        return [{"op": "remove_entry", "id": step.target}], ""
    if step.op == "RESTORE_ENTRY":
        return [{"op": "restore_entry", "id": step.target}], ""
    if step.op == "SET_PAGE_TARGET":
        return [{"op": "set_page_target", "pages": step.params.get("pages", 1)}], ""
    if step.op == "REMOVE_SKILL":
        ops = []
        for s in c["sections"]:
            for cat in s.get("categories") or []:
                keep = [x for x in cat["items"] if _norm(x) != _norm(step.target)]
                if len(keep) != len(cat["items"]):
                    ops.append({"op": "set_skills", "id": cat["id"], "items": keep or cat["items"][:1]})
        return ops, "" if ops else f"{step.target} is not in the skills section"
    if step.op == "ADD_SKILL":
        return [], (f"adding “{step.target}” needs evidence: add it to your profile skills or a verified project first")
    if step.op == "REDUCE_CONTENT":
        return reduce_content(ctx)
    if step.op == "LIMIT_ENTRIES":
        sec = next((x for x in c["sections"] if x["id"] == step.target), None)
        entries = (sec or {}).get("entries") or []
        n = int(step.params.get("n", len(entries)))
        if len(entries) <= n:
            return [], f"there are already {len(entries)}"
        # keep the first n in the current order (the user's own ordering decides what matters most)
        return [{"op": "remove_entry", "id": e["id"]} for e in entries[n:]], ""
    return [], "not supported"


def reduce_content(ctx: Context) -> tuple[list[dict], str]:
    """Removes bullets (and whole project entries) that support none of the job's requirements."""
    focus = set(ctx.focus)
    if not focus:
        return [], "this resume has no job requirements to measure relevance against"
    ops = []
    for s in ctx.content["sections"]:
        if s.get("removed") or s["kind"] not in ("projects", "experience"):
            continue
        entries = s.get("entries") or []
        for e in entries:
            bullets = [b for b in e.get("bullets") or [] if not b.get("tech_line")]
            hits = [b for b in bullets if {m.key for m in find_skills(b["text"])} & focus]
            name_hits = {m.key for m in find_skills(" ".join(b["text"] for b in e.get("bullets") or []))} & focus
            if s["kind"] == "projects" and not name_hits and len(entries) > 2:
                ops.append({"op": "remove_entry", "id": e["id"]})
                continue
            for b in bullets:
                if b not in hits and len(hits) >= 1 and len(bullets) - 1 >= 1 and b.get("origin") != "user":
                    ops.append({"op": "remove_bullet", "id": b["id"]})
    return ops, "" if ops else "everything on the resume supports at least one of the job's requirements"


def describe_text_change(ctx: Context, step: Step, before: dict[str, str], ops: list[dict]) -> str:
    els = index(ctx.content)
    if not ops:
        return ""
    if len(ops) == 1:
        eid = ops[0]["id"]
        el = els.get(eid, {})
        what = _label_of(ctx, eid, el)
        b, a = _lines_of(ctx, before.get(eid, "")), _lines_of(ctx, ops[0]["text"])
        verb = {
            "EXPAND_TEXT": "Expanded",
            "SHORTEN_TEXT": "Shortened",
            "RESIZE_TEXT": "Resized",
            "CHANGE_EMPHASIS": "Refocused",
            "REWRITE_TEXT": "Rewrote",
        }.get(step.op, "Edited")
        size = f" from {b} to {a} line{'s' if a != 1 else ''}" if b != a else ""
        focus = f" around {step.params['focus']}" if step.params.get("focus") else ""
        return f"{verb} {what}{size}{focus}"
    verb = {"EXPAND_TEXT": "Expanded", "SHORTEN_TEXT": "Tightened", "CHANGE_EMPHASIS": "Refocused"}.get(
        step.op, "Rewrote"
    )
    return f"{verb} {len(ops)} items"


def _label_of(ctx: Context, eid: str, el: dict) -> str:
    if el.get("type") == "paragraph":
        sec = next((s for s in ctx.content["sections"] if s["id"] == el.get("section")), None)
        return f"the {(sec.get('title_text') or sec['title']).lower() if sec else 'paragraph'}"
    if el.get("type") == "bullet":
        entry = next(
            (e for s in ctx.content["sections"] for e in s.get("entries") or [] if e["id"] == el.get("entry")), None
        )
        name = entry["fields"].get("short_name") or entry["fields"].get("primary") if entry else ""
        return f"{'the selected bullet' if eid in ctx.selection else 'a bullet'}{f' in {name}' if name else ''}"
    return "the item"
