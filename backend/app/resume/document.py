"""The canonical resume document and the targeted operations that edit it.

Every generated resume is a structured document (ResumeVersion.content). The visual editor, the editing
assistant and the PDF renderer all read and write this one document; nothing keeps a separate copy.

Each editable element has a stable id:

    header.name            header.link.<kind>          section.<n>.title
    summary.text           <entry id>.field.<role>     <entry id>.b<k>   (bullets)
    <section id>.i<k>      skills.<slug>               <section id>.row<r>.cell<c>

Edits are expressed as operations (update_text, move_entry, set_link, ...). Operations change only the
elements they name, record provenance, and are applied in a single transaction that becomes a new revision.

Provenance of text:
    profile / template   the user's own wording (VERIFIED_PROFILE / USER_PROVIDED)
    evidence             drafted from verified repository evidence (AI_GENERATED_FROM_VERIFIED_EVIDENCE)
    rewritten            reworded by the model, checked against its source
    user                 typed by the user in the editor (USER_EDITED) - preserved by broad rewrites
"""

import copy
import re

from app.core.errors import AppError

PROVENANCE_LABEL = {
    "profile": "VERIFIED_PROFILE", "template": "USER_PROVIDED", "evidence": "AI_GENERATED_FROM_VERIFIED_EVIDENCE",
    "rewritten": "AI_GENERATED_FROM_VERIFIED_EVIDENCE", "user": "USER_EDITED",
}
OPS = ("update_text", "add_bullet", "remove_bullet", "move_bullet", "move_entry", "remove_entry", "restore_entry",
       "set_link", "remove_section", "restore_section", "set_page_target", "set_skills", "update_field")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "x"


def ensure_ids(content: dict, header: dict | None = None) -> dict:
    """Assigns stable ids to every element that does not have one yet (idempotent)."""
    content.setdefault("header", {})
    h = content["header"]
    if header:
        h.setdefault("name", header.get("name", ""))
        h.setdefault("original_name", header.get("name", ""))
        if "links" not in h:
            h["links"] = [{"id": f"header.link.{lk['kind']}", "kind": lk["kind"], "url": lk["url"],
                           "text": lk.get("text", ""), "mode": "template"} for lk in header.get("links", [])
                          if lk.get("kind") in ("linkedin", "github", "website", "portfolio")]
            for lk in h["links"]:
                if lk["kind"] == "website":
                    lk["kind"], lk["id"] = "portfolio", "header.link.portfolio"
    seen: set[str] = set()
    for sec in content.get("sections", []):
        sid = sec.setdefault("id", f"{sec['kind']}" if sec["kind"] in ("summary", "skills") else
                             f"section.{sec['index']}")
        sec.setdefault("title_id", f"section.{sec['index']}.title")
        if sec.get("layout") == "paragraph":
            sec.setdefault("text_id", "summary.text" if sec["kind"] == "summary" else f"{sid}.text")
        for e in sec.get("entries") or []:
            if "id" not in e:
                base = f"{sec['kind']}.{_slug(e.get('fields', {}).get('short_name') or e.get('fields', {}).get('primary', ''))}"
                eid, n = base, 2
                while eid in seen:
                    eid, n = f"{base}-{n}", n + 1
                e["id"] = eid
            seen.add(e["id"])
            e.setdefault("next_bullet", 1)
            for b in e.get("bullets") or []:
                if "id" not in b:
                    b["id"] = f"{e['id']}.b{e['next_bullet']}"
                    e["next_bullet"] += 1
        if sec.get("layout") == "items":
            ids = sec.setdefault("item_ids", [])
            while len(ids) < len(sec.get("items") or []):
                ids.append(f"{sid}.i{len(ids) + 1}")
        for c in sec.get("categories") or []:
            c.setdefault("id", f"skills.{_slug(c['name'])}")
    return content


def index(content: dict) -> dict[str, dict]:
    """Maps element ids to what they are, for the editor and the assistant."""
    out: dict[str, dict] = {"header.name": {"type": "header_name", "text": content.get("header", {}).get("name", "")}}
    for lk in content.get("header", {}).get("links", []):
        out[lk["id"]] = {"type": "link", "kind": lk["kind"], "mode": lk["mode"]}
    for sec in content.get("sections", []):
        out[sec["title_id"]] = {"type": "section_title", "section": sec["id"], "text": sec.get("title_text", sec["title"])}
        if sec.get("layout") == "paragraph":
            out[sec["text_id"]] = {"type": "paragraph", "section": sec["id"], "text": sec.get("text", "")}
        for e in sec.get("entries") or []:
            out[e["id"]] = {"type": "entry", "section": sec["id"], "text": e.get("fields", {}).get("primary", "")}
            for role in ("primary", "secondary", "dates", "location"):
                if role in (e.get("fields") or {}):
                    out[f"{e['id']}.field.{role}"] = {"type": "field", "entry": e["id"], "role": role,
                                                      "text": e["fields"][role]}
            for b in e.get("bullets") or []:
                out[b["id"]] = {"type": "bullet", "entry": e["id"], "section": sec["id"], "text": b["text"],
                                "origin": b.get("origin"), "tech_line": bool(b.get("tech_line"))}
        for i, iid in enumerate(sec.get("item_ids") or []):
            if i < len(sec.get("items") or []):
                out[iid] = {"type": "item", "section": sec["id"], "pos": i, "text": sec["items"][i]}
        for c in sec.get("categories") or []:
            out[c["id"]] = {"type": "skills", "section": sec["id"], "text": ", ".join(c["items"])}
    return out


def _find_section(content: dict, sid: str) -> dict:
    for s in content["sections"]:
        if s.get("id") == sid:
            return s
    raise AppError(f"Unknown section {sid}.", code="unknown_element")


def _find_entry(content: dict, eid: str) -> tuple[dict, dict]:
    for s in content["sections"]:
        for e in s.get("entries") or []:
            if e["id"] == eid:
                return s, e
    raise AppError(f"Unknown entry {eid}.", code="unknown_element")


def _find_bullet(content: dict, bid: str) -> tuple[dict, dict, int]:
    for s in content["sections"]:
        for e in s.get("entries") or []:
            for i, b in enumerate(e.get("bullets") or []):
                if b["id"] == bid:
                    return e, b, i
    raise AppError(f"Unknown bullet {bid}.", code="unknown_element")


def _clean(text: str, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) > limit:
        raise AppError(f"That text is too long (max {limit} characters).", code="text_too_long")
    return text


def apply_ops(content: dict, ops: list[dict], source: str) -> tuple[dict, list[str]]:
    """Applies operations to a copy of the document. source: "user" (typed in the editor) or "assistant".
    Returns (new content, ids of elements that changed)."""
    doc = copy.deepcopy(content)
    ensure_ids(doc)
    changed: list[str] = []
    origin = "user" if source == "user" else "rewritten"
    for op in ops[:100]:
        kind = op.get("op")
        if kind not in OPS:
            raise AppError(f"Unknown operation {kind}.", code="invalid_operation")
        tid = op.get("id", "")
        if kind == "update_text":
            text = _clean(op.get("text", ""))
            if tid == "header.name":
                doc["header"]["name"] = text
            elif tid.endswith(".title"):
                sec = next(s for s in doc["sections"] if s["title_id"] == tid)
                sec["title_text"] = text
            elif tid.endswith(".text"):
                sec = next((s for s in doc["sections"] if s.get("text_id") == tid), None)
                if sec is None:
                    raise AppError("Unknown text element.", code="unknown_element")
                if not text:
                    raise AppError("Use “remove section” to drop the summary.", code="empty_text")
                sec["text"] = text
                sec["text_origin"] = origin
                sec.pop("unchanged", None)
            elif ".field." in tid:
                eid, role = tid.split(".field.")
                _, e = _find_entry(doc, eid)
                e["fields"][role] = text
                e["rewrite_heading"] = True
            elif re.search(r"\.i\d+$", tid):
                sec = next(s for s in doc["sections"] if tid in (s.get("item_ids") or []))
                pos = sec["item_ids"].index(tid)
                sec["items"][pos] = text
                sec["items_unchanged"] = False
            else:
                e, b, _ = _find_bullet(doc, tid)
                if not text:
                    raise AppError("A bullet can't be empty; remove it instead.", code="empty_text")
                if b.get("tech_line") and not re.match(r"^\**\s*(technologies|tech stack|tools)", text, re.I):
                    text = "**Technologies:** " + text
                b["text"] = text
                b["origin"] = origin
        elif kind == "update_field":
            _, e = _find_entry(doc, tid)
            e["fields"][op["role"]] = _clean(op.get("text", ""), 200)
            e["rewrite_heading"] = True
        elif kind == "add_bullet":
            _, e = _find_entry(doc, tid)
            if e.get("bullets") is None:
                raise AppError("This entry has no bullet list in the template.", code="no_bullets")
            text = _clean(op.get("text", ""))
            if not text:
                raise AppError("Enter the bullet text.", code="empty_text")
            b = {"id": f"{e['id']}.b{e['next_bullet']}", "text": text, "original": text, "origin": origin}
            e["next_bullet"] += 1
            pos = op.get("position")
            content_count = sum(1 for x in e["bullets"] if not x.get("tech_line"))
            pos = content_count if pos is None else max(0, min(int(pos), content_count))
            e["bullets"].insert(pos, b)
            tid = b["id"]
        elif kind == "remove_bullet":
            e, b, i = _find_bullet(doc, tid)
            if sum(1 for x in e["bullets"] if not x.get("tech_line")) <= 1 and not b.get("tech_line"):
                raise AppError("An entry needs at least one bullet; remove the entry instead.", code="last_bullet")
            e["bullets"].pop(i)
        elif kind == "move_bullet":
            e, b, i = _find_bullet(doc, tid)
            e["bullets"].pop(i)
            e["bullets"].insert(max(0, min(int(op.get("position", 0)), len(e["bullets"]))), b)
        elif kind == "move_entry":
            sec, e = _find_entry(doc, tid)
            sec["entries"].remove(e)
            sec["entries"].insert(max(0, min(int(op.get("position", 0)), len(sec["entries"]))), e)
        elif kind == "remove_entry":
            sec, e = _find_entry(doc, tid)
            if len(sec["entries"]) <= 1:
                raise AppError("This is the last entry; remove the section instead.", code="last_entry")
            sec["entries"].remove(e)
            sec.setdefault("removed_entries", []).append(e)
        elif kind == "restore_entry":
            sec = next((s for s in doc["sections"] if any(x["id"] == tid for x in s.get("removed_entries") or [])),
                       None)
            if sec is None:
                raise AppError("Nothing to restore.", code="unknown_element")
            e = next(x for x in sec["removed_entries"] if x["id"] == tid)
            sec["removed_entries"].remove(e)
            sec["entries"].append(e)
        elif kind == "set_link":
            link_kind, mode = op.get("kind"), op.get("mode")
            if link_kind not in ("portfolio", "github", "linkedin") or mode not in ("include", "exclude", "template"):
                raise AppError("Unknown link change.", code="invalid_operation")
            links = doc["header"].setdefault("links", [])
            lk = next((x for x in links if x["kind"] == link_kind), None)
            if lk is None:
                lk = {"id": f"header.link.{link_kind}", "kind": link_kind, "url": "", "text": "", "mode": "template"}
                links.append(lk)
            lk["mode"] = mode
            tid = lk["id"]
        elif kind in ("remove_section", "restore_section"):
            sec = _find_section(doc, tid)
            sec["removed"] = kind == "remove_section"
        elif kind == "set_page_target":
            pages = int(op.get("pages", 1))
            if pages not in (1, 2, 3):
                raise AppError("Choose 1, 2 or 3 pages.", code="invalid_operation")
            doc["page_target"] = pages
            tid = "page_target"
        elif kind == "set_skills":
            c = next((c for s in doc["sections"] for c in s.get("categories") or [] if c["id"] == tid), None)
            if c is None:
                raise AppError("Unknown skills category.", code="unknown_element")
            items = [_clean(x, 80) for x in (op.get("items") or []) if str(x).strip()][:40]
            if not items:
                raise AppError("A skills line needs at least one skill.", code="empty_text")
            c["items"] = items
            c["origin"] = origin
        changed.append(tid)
    return doc, changed


def user_claims(content: dict) -> list[str]:
    """Numbers or impact claims the user typed that CareerPilot cannot verify."""
    from app.resume.truth import IMPACT_TERMS, NUMBER_RX

    flagged = []
    for s in content.get("sections", []):
        for e in s.get("entries") or []:
            for b in e.get("bullets") or []:
                if b.get("origin") != "user":
                    continue
                orig = b.get("original", "")
                new_nums = {m.group().strip() for m in NUMBER_RX.finditer(b["text"])} - \
                    {m.group().strip() for m in NUMBER_RX.finditer(orig)}
                terms = [t for t in IMPACT_TERMS if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", b["text"], re.I)
                         and not re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", orig, re.I)]
                if new_nums or terms:
                    flagged.append(b["text"][:100])
    return flagged
