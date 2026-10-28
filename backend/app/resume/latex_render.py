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
    elif spec.get("rewrite_url") and "url" in slots and fields.get("url"):
        # only the link changes (a project linked to the GitHub profile now links its repository)
        reps.append((slots["url"][0], slots["url"][1], escape_url(fields["url"])))

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


def render_latex(
    src: str,
    structure: dict,
    instructions: list[dict],
    *,
    header_links: dict | None = None,
    remove_sections: list[int] | None = None,
    section_titles: dict[int, str] | None = None,
    header_name: str | None = None,
) -> str:
    """instructions: [{"index": section index, ...layout-specific content}]
    header_links: {"portfolio"|"github"|"linkedin": {"mode": "include"|"exclude", "url": str}}
    remove_sections: section indexes to drop entirely (command and content)."""
    sections = {s["index"]: s for s in structure["sections"]}
    reps: list[tuple[int, int, str]] = []
    removed = set(remove_sections or [])
    for idx in removed:
        sec = sections.get(idx)
        if sec is not None and "command_start" in sec:
            reps.append((sec["command_start"], sec["span"][1], ""))
    if header_links:
        reps += header_link_edits(src, structure["header"]["span"], header_links)
    for idx, title in (section_titles or {}).items():
        sec = sections.get(idx)
        if sec is not None and "title_span" in sec and idx not in removed and title.strip():
            reps.append((sec["title_span"][0], sec["title_span"][1], escape(title.strip())))
    if header_name and header_name.strip() and structure["header"].get("name"):
        hs, he = structure["header"]["span"]
        old = structure["header"]["name"]
        pos = src.find(old, hs, he)
        if pos >= 0 and header_name.strip() != old:
            reps.append((pos, pos + len(old), escape(header_name.strip())))
    for ins in instructions:
        if ins["index"] in removed:
            continue
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


# --- header links -----------------------------------------------------------------------------------

_SEP_ONLY = re.compile(r"^[\s|$~]*(\\(?:vert|textbar|quad|qquad|hspace\{[^}]*\}|,|;|\s))*[\s|$~]*$")
_ICON = re.compile(r"\\fa[A-Z][A-Za-z]*\*?")


def link_kind(url: str, portfolio_url: str = "") -> str:
    low = url.lower()
    if low.startswith("mailto:"):
        return "email"
    if low.startswith("tel:"):
        return "phone"
    if "linkedin." in low:
        return "linkedin"
    if "github.com" in low:
        return "github"
    return "portfolio"


def _header_hrefs(src: str, span: list[int]) -> list[tuple[int, int, str]]:
    from app.resume.latex_syntax import comment_mask, iter_macros

    mask = comment_mask(src)
    out = []
    for c in iter_macros(src, span[0], span[1], mask):
        if c.name == "href" and len(c.args) >= 2:
            out.append((c.start, c.args[1].end + 1, c.args[0].inner(src).strip()))
    return out


def header_link_edits(src: str, span: list[int], wanted: dict) -> list[tuple[int, int, str]]:
    """Removes or adds header links while keeping the template's own separators, icons and styling."""
    links = _header_hrefs(src, span)
    reps: list[tuple[int, int, str]] = []
    present = {link_kind(u): (s, e, u) for s, e, u in links}
    for kind, spec in wanted.items():
        mode, url = spec.get("mode"), spec.get("url", "")
        if mode == "exclude" and kind in present:
            s, e, _ = present[kind]
            i = next(n for n, lk in enumerate(links) if lk[0] == s)
            left = src[links[i - 1][1] : s] if i > 0 else None
            right = src[e : links[i + 1][0]] if i + 1 < len(links) else None
            if left is not None and _SEP_ONLY.match(left):
                reps.append((links[i - 1][1], e, ""))
            elif right is not None and _SEP_ONLY.match(right):
                reps.append((s, links[i + 1][0], ""))
            else:
                reps.append((s, e, ""))
        elif mode == "include" and kind not in present and url and links:
            # clone the style of an existing profile link (GitHub > LinkedIn > last link)
            proto = present.get("github") or present.get("linkedin") or (links[-1][0], links[-1][1], links[-1][2])
            ps, pe, purl = proto
            clone = src[ps:pe].replace(purl, escape_url(url), 1)
            clone = _ICON.sub(r"\\faGlobe", clone, count=1)
            label = {"portfolio": "Portfolio", "github": "GitHub", "linkedin": "LinkedIn"}[kind]
            clone = _relabel(clone, label)
            last_s, last_e, _ = links[-1]
            sep = (
                src[links[-2][1] : links[-1][0]]
                if len(links) >= 2 and _SEP_ONLY.match(src[links[-2][1] : links[-1][0]])
                else " $|$ "
            )
            reps.append((last_e, last_e, sep + clone))
    return reps


def _relabel(href_call: str, label: str) -> str:
    """Replaces the visible text of a cloned \\href (keeping its icon and styling macros) with `label`."""
    m = re.match(r"(\\href\{[^{}]*\}\{)(.*)(\})\s*$", href_call, re.S)
    if not m:
        return href_call
    head, body, tail = m.groups()
    inner = re.search(r"\\(?:underline|textit|textbf|emph|texttt)\{([^{}]*)\}\s*$", body)
    if inner:
        body = body[: inner.start(1)] + escape(label) + body[inner.end(1) :]
    else:
        run = re.search(r"([^\\{}\s][^\\{}]*)$", body)
        body = body[: run.start(1)] + escape(label) if run else body + " " + escape(label)
    return head + body + tail
