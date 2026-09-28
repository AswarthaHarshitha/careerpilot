"""Writes tailored content back into the user's own LaTeX template.

Only content spans recorded by the parser are replaced. Headings for entries that already exist in the
template are reused verbatim; a new entry (for example a project that was not on the original resume) is
cloned from the first entry of the same section, so it inherits exactly the same macros and spacing.
"""

import re

from app.resume.latex_syntax import escape, escape_url


class RenderError(ValueError):
    pass


def inline(text: str) -> str:
    """Escapes text, allowing **bold** as the only markup."""
    parts = re.split(r"(\*\*[^*]+\*\*)", text or "")
    out = []
    for p in parts:
        if p.startswith("**") and p.endswith("**") and len(p) > 4:
            out.append(r"\textbf{" + escape(p[2:-2]) + "}")
        else:
            out.append(escape(p))
    return "".join(out)


def _apply(src: str, reps: list[tuple[int, int, str]]) -> str:
    reps = sorted(reps, key=lambda r: r[0])
    for a, b in zip(reps, reps[1:], strict=False):
        if a[1] > b[0]:
            raise RenderError("Overlapping template edits; the template structure is ambiguous.")
    out, pos = [], 0
    for start, end, text in reps:
        out.append(src[pos:start])
        out.append(text)
        pos = end
    out.append(src[pos:])
    return "".join(out)


def _render_items(src: str, proto_item: dict, texts: list[str], separator: str) -> str:
    ps, pe = proto_item["span"]
    in_s, in_e = proto_item["inner"]
    rendered = []
    for t in texts:
        rendered.append(src[ps:in_s] + inline(t) + src[in_e:pe])
    return separator.join(rendered)


def _render_entry(src: str, base: dict, spec: dict, is_clone: bool, section: dict) -> str:
    es, ee = base["span"]
    reps: list[tuple[int, int, str]] = []
    fields = spec.get("fields") or {}
    slots = base.get("slots", {})

    if is_clone or spec.get("rewrite_heading"):
        for role in ("primary", "dates", "secondary", "location", "tech"):
            if role in slots and (is_clone or role in fields):
                reps.append((slots[role][0], slots[role][1], inline(fields.get(role, ""))))
        if "url" in slots:
            url = fields.get("url", "")
            if url:
                reps.append((slots["url"][0], slots["url"][1], escape_url(url)))
            elif "link_call" in slots:
                reps.append((slots["link_call"][0], slots["link_call"][1], ""))

    bullets = spec.get("bullets")
    if bullets is not None and base.get("items_region"):
        bullets = [b for b in bullets if b and b.strip()]
        if not bullets:
            raise RenderError("An entry must keep at least one bullet.")
        rs, re_ = base["items_region"]
        sep = base.get("item_separator") or _section_item_separator(section)
        reps.append((rs, re_, _render_items(src, base["items"][0], bullets, sep)))

    local = [(a - es, b - es, t) for a, b, t in reps if es <= a and b <= ee]
    return _apply(src[es:ee], local)


def _section_item_separator(section: dict) -> str:
    for e in section.get("entries", []):
        if len(e.get("items", [])) >= 2:
            return e["item_separator"]
    return "\n"


def render_latex(src: str, structure: dict, instructions: list[dict]) -> str:
    """instructions: [{"index": section index, ...layout-specific content}]"""
    sections = {s["index"]: s for s in structure["sections"]}
    reps: list[tuple[int, int, str]] = []
    for ins in instructions:
        sec = sections.get(ins["index"])
        if sec is None:
            raise RenderError(f"Section {ins['index']} does not exist in the template.")
        layout = sec["layout"]
        if layout == "entries" and "entries" in ins:
            entries = sec["entries"]
            if not entries or not ins["entries"]:
                continue
            rendered = []
            for spec in ins["entries"]:
                src_idx = spec.get("source_index")
                if src_idx is not None:
                    rendered.append(_render_entry(src, entries[src_idx], spec, False, sec))
                else:
                    proto = _clone_prototype(entries, spec)
                    rendered.append(_render_entry(src, proto, spec, True, sec))
            region = (entries[0]["span"][0], entries[-1]["span"][1])
            reps.append((region[0], region[1], sec["entry_separator"].join(rendered)))
        elif layout == "skills" and "categories" in ins:
            wanted = {_norm(c["name"]): c["items"] for c in ins["categories"]}
            for cat in sec["skills"]:
                items = wanted.get(_norm(cat["name"]))
                if items:
                    reps.append((cat["span"][0], cat["span"][1], escape(", ".join(items))))
        elif layout == "items" and "items" in ins:
            texts = [t for t in ins["items"] if t and t.strip()]
            if texts:
                rs, re_ = sec["items_region"]
                reps.append((rs, re_, _render_items(src, sec["items"][0], texts, sec["item_separator"])))
        elif layout == "paragraph" and "text" in ins:
            if ins["text"].strip():
                s, e = sec["paragraph"]["span"]
                reps.append((s, e, inline(ins["text"].strip())))
    return _apply(src, reps)


def _clone_prototype(entries: list[dict], spec: dict) -> dict:
    """Prefers a prototype whose heading has the same optional parts (link, tech) as the new entry."""
    want_url = bool((spec.get("fields") or {}).get("url"))
    for e in entries:
        if bool(e.get("slots", {}).get("url")) == want_url and e.get("items"):
            return e
    for e in entries:
        if e.get("items"):
            return e
    return entries[0]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())
