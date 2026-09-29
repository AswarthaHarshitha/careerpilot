"""Parses a LaTeX resume into a structured, span-annotated representation.

The parser does not assume a particular template. It finds the document's section command, classifies each
section by its title, and learns how that section lays out its content:

* ``entries``   - repeated headings (a macro such as ``\\resumeSubheading{..}{..}`` or a bold line) each followed
                  by bullet items (``\\resumeItem{..}`` or ``\\item ...``);
* ``skills``    - ``\\textbf{Category}{: a, b, c}`` / ``\\textbf{Category:} a, b`` lines;
* ``items``     - a flat bullet list (achievements, certifications);
* ``paragraph`` - running text (summary).

Every piece of content is recorded with its exact character span in the source, so rendering can replace
content surgically and leave the template's own markup, spacing and macros untouched.
"""

import re
from collections import Counter

from app.resume.latex_syntax import (
    Arg,
    MacroCall,
    comment_mask,
    find_hrefs,
    iter_macros,
    match_brace,
    read_args,
    to_plain,
)

SECTION_KINDS: list[tuple[str, re.Pattern]] = [
    ("summary", re.compile(r"summary|profile|objective|about me|about", re.I)),
    ("experience", re.compile(r"experience|employment|work history|internships?|professional", re.I)),
    ("projects", re.compile(r"project", re.I)),
    ("skills", re.compile(r"skill|technolog|competenc|tools|tech stack|expertise", re.I)),
    ("education", re.compile(r"education|academic|qualification", re.I)),
    ("certifications", re.compile(r"certif|licen", re.I)),
    ("achievements", re.compile(r"achievement|award|honou?r|accomplish", re.I)),
    ("publications", re.compile(r"publication|research|paper", re.I)),
    ("leadership", re.compile(r"leadership|activit|extracurricular|volunteer|position|involvement", re.I)),
]

# Macros that format text rather than start a resume entry.
FORMATTING = {
    "textbf",
    "textit",
    "emph",
    "underline",
    "href",
    "url",
    "small",
    "large",
    "Large",
    "LARGE",
    "huge",
    "Huge",
    "footnotesize",
    "scriptsize",
    "normalsize",
    "tiny",
    "vspace",
    "hspace",
    "hfill",
    "textcolor",
    "color",
    "begin",
    "end",
    "item",
    "textsc",
    "scshape",
    "bfseries",
    "itshape",
    "raggedright",
    "centering",
    "newline",
    "linebreak",
    "noindent",
    "par",
    "fontsize",
    "selectfont",
    "textsf",
    "texttt",
    "mbox",
    "makebox",
    "raisebox",
    "setlength",
    "addtolength",
    "rule",
    "titlerule",
    "quad",
    "qquad",
    "textbar",
    "vert",
    "cdot",
    "bullet",
    "faGithub",
    "faLinkedin",
    "faPhone",
    "faEnvelope",
    "faGlobe",
    "faExternalLink",
    "faLink",
    "input",
    "label",
}
DATE_RX = re.compile(
    r"\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s*)?(?:19|20)\d{2}\b|\bPresent\b|"
    r"\bCurrent\b|\bOngoing\b|\bExpected\b|\bNow\b",
    re.I,
)
GRADE_RX = re.compile(
    r"\s*(?:[—–|,-]\s*)?((?:C?GPA|CPI|SGPA|Percentage|Score|Grade|Marks)\s*[:\-]?\s*[\d.]+\s*(?:/\s*[\d.]+)?"
    r"%?|[\d.]+\s*/\s*(?:10|4|100|1000|600)(?:\.0)?|\d{2,3}(?:\.\d+)?\s*%)\s*$",
    re.I,
)


def classify_section(title: str) -> str:
    for kind, rx in SECTION_KINDS:
        if rx.search(title):
            return kind
    return "other"


def _is_section_macro(name: str) -> bool:
    low = name.lower().rstrip("*")
    return low.endswith("section") and not low.startswith("sub") and low not in ("titlesection",)


def parse_latex_template(src: str) -> dict:
    mask = comment_mask(src)
    begin = _find_unmasked(src, r"\begin{document}", mask)
    end_doc = _find_unmasked(src, r"\end{document}", mask)
    if begin < 0 or end_doc < 0:
        raise TemplateParseError(
            "This file is not a complete LaTeX document (no \\begin{document} ... \\end{document})."
        )
    body_start = begin + len(r"\begin{document}")

    section_calls = [c for c in iter_macros(src, body_start, end_doc, mask) if _is_section_macro(c.name) and c.args]
    if not section_calls:
        raise TemplateParseError("No sections were found. CareerPilot looks for \\section{...} style headings.")

    header_span = [body_start, section_calls[0].start]
    sections = []
    for i, call in enumerate(section_calls):
        content_start = call.end
        content_end = section_calls[i + 1].start if i + 1 < len(section_calls) else end_doc
        title = to_plain(call.args[0].inner(src))
        kind = classify_section(title)
        sec = {
            "index": i,
            "title": title,
            "kind": kind,
            "command": call.name,
            "command_start": call.start,
            "title_span": [call.args[0].start + 1, call.args[0].end],
            "span": [content_start, content_end],
        }
        sec.update(_parse_section_content(src, mask, content_start, content_end, kind))
        sections.append(sec)

    return {
        "format": "latex",
        "body_span": [body_start, end_doc],
        "header": _parse_header(src, header_span),
        "sections": sections,
    }


class TemplateParseError(ValueError):
    pass


def _find_unmasked(src: str, needle: str, mask: list[bool]) -> int:
    start = 0
    while True:
        i = src.find(needle, start)
        if i < 0 or not mask[i]:
            return i
        start = i + 1


# --- header ----------------------------------------------------------------------------------------

EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RX = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{3,5}")


def _parse_header(src: str, span: list[int]) -> dict:
    raw = src[span[0] : span[1]]
    plain = to_plain(raw)
    lines = [ln.strip(" |") for ln in plain.splitlines() if ln.strip(" |")]
    links = []
    email = ""
    for url, text in find_hrefs(raw):
        if url.startswith("mailto:"):
            email = email or url[7:]
            continue
        low = url.lower()
        kind = (
            "linkedin"
            if "linkedin." in low
            else "github"
            if "github." in low
            else ("phone" if low.startswith("tel:") else "website")
        )
        links.append({"url": url, "text": text, "kind": kind})
    if not email:
        m = EMAIL_RX.search(plain)
        email = m.group() if m else ""
    phone = ""
    for ln in lines:
        m = PHONE_RX.search(ln.replace(email, "") if email else ln)
        if m and sum(ch.isdigit() for ch in m.group()) >= 8:
            phone = m.group().strip()
            break
    name = lines[0] if lines else ""
    return {"span": span, "name": name, "email": email, "phone": phone, "links": links}


# --- section content ------------------------------------------------------------------------------


def _parse_section_content(src: str, mask: list[bool], start: int, end: int, kind: str) -> dict:
    calls = list(iter_macros(src, start, end, mask))
    items = _find_items(src, mask, calls, start, end)

    if kind == "skills":
        cats = _parse_skill_lines(src, mask, calls, start, end) or _tabular_skills(src, mask, calls, start, end)
        if cats:
            return {"layout": "skills", "skills": cats}

    if kind in ("experience", "projects", "leadership", "publications", "education", "other"):
        tab = _tabular_entries(src, mask, calls, start, end, kind, items)
        if tab:
            return {"layout": "entries", "entries": tab, "entry_separator": _entry_separator(src, tab)}

    headings = _find_headings(calls, items, kind)
    if headings:
        entries = _build_entries(src, mask, calls, headings, items, end, kind)
        if entries:
            return {"layout": "entries", "entries": entries, "entry_separator": _entry_separator(src, entries)}

    blocks = _itemize_blocks(src, mask, calls, start, end)
    if blocks and kind in ("experience", "projects", "education", "leadership", "publications"):
        entries = _block_entries(src, mask, blocks, items, start, kind)
        if entries:
            return {"layout": "entries", "entries": entries, "entry_separator": _entry_separator(src, entries)}

    if items:
        return {
            "layout": "items",
            "items": [_item_dict(src, it) for it in items],
            "items_region": [items[0]["start"], items[-1]["end"]],
            "item_separator": _item_separator(src, items),
        }

    if kind == "education":
        rows = _table_rows(src, mask, calls, start, end)
        if rows:
            return {"layout": "table", "rows": rows}

    para = _paragraph_span(src, mask, start, end)
    if para:
        return {"layout": "paragraph", "paragraph": {"span": para, "text": to_plain(src[para[0] : para[1]])}}
    return {"layout": "raw"}


def _find_items(src, mask, calls: list[MacroCall], start, end) -> list[dict]:
    """Bullet items: one-argument \\...item... macros, or plain \\item lines."""
    items: list[dict] = []
    item_macro_names = Counter(
        c.name
        for c in calls
        if "item" in c.name.lower() and c.name != "item" and len(c.args) == 1 and "list" not in c.name.lower()
    )
    for c in calls:
        if c.name in item_macro_names and len(c.args) == 1:
            a = c.args[0]
            items.append({"start": c.start, "end": a.end + 1, "inner": [a.start + 1, a.end], "macro": c.name})
    if items:
        return items
    plain_items = [c for c in calls if c.name == "item"]
    for idx, c in enumerate(plain_items):
        text_start = c.name_end
        # skip optional [label] and whitespace
        m = re.match(r"\s*(\[[^\]]*\])?\s*", src[text_start:end])
        text_start += m.end() if m else 0
        nxt = plain_items[idx + 1].start if idx + 1 < len(plain_items) else end
        stop = nxt
        for pat in (r"\end{itemize}", r"\end{enumerate}", r"\end{description}"):
            j = src.find(pat, text_start, nxt)
            if j >= 0:
                stop = min(stop, j)
        text_end = stop
        while text_end > text_start and (src[text_end - 1].isspace() or mask[text_end - 1]):
            text_end -= 1
        inner = [text_start, text_end]
        # "\item{text}" or "\item \small{text}" - edit inside the braces
        seg = src[text_start:text_end]
        wm = re.match(r"^(\\[A-Za-z]+\s*)?\{", seg)
        if wm:
            ob = text_start + wm.end() - 1
            cb = match_brace(src, ob, mask)
            if cb == text_end - 1:
                inner = [ob + 1, cb]
        if text_end > text_start:
            items.append({"start": c.start, "end": text_end, "inner": inner, "macro": "item"})
    return items


TECH_LINE = re.compile(r"^\s*(?:\\textbf\{)?\s*(technologies|tech stack|tools|stack|built with)\s*:?", re.I)


def _item_dict(src, it) -> dict:
    raw = src[it["inner"][0] : it["inner"][1]]
    d = {"span": [it["start"], it["end"]], "inner": it["inner"], "macro": it["macro"], "text": to_plain(raw)}
    if TECH_LINE.match(raw):
        d["tech_line"] = True
    return d


# --- table-based layouts (tabular / tabularx rows) --------------------------------------------------

TABULAR_ENVS = ("tabularx", "tabular", "tabular*", "longtable", "supertabular")


def _envs(src, mask, calls, start, end, names=TABULAR_ENVS) -> list[tuple[MacroCall, int, int]]:
    """Top-level environments of the given names: (begin call, body start, end index after \\end{...})."""
    out, depth, cur = [], 0, None
    for c in calls:
        if c.start < start or c.start >= end or not c.args:
            continue
        env = c.args[0].inner(src)
        if c.name == "begin" and env in names:
            if depth == 0:
                cur = c
            depth += 1
        elif c.name == "end" and env in names and depth:
            depth -= 1
            if depth == 0 and cur is not None:
                body_start = cur.args[-1].end + 1
                out.append((cur, body_start, c.start, c.args[0].end + 1))
                cur = None
    return out


def _split_top(src, mask, a: int, b: int, sep: str) -> list[tuple[int, int]]:
    """Splits src[a:b] at top-level occurrences of sep (\\\\ or &), ignoring braces and comments."""
    parts, depth, i, last = [], 0, a, a
    while i < b:
        if mask[i]:
            i += 1
            continue
        ch = src[i]
        if ch == "\\" and src.startswith("\\\\", i) and sep == "\\\\" and depth == 0:
            parts.append((last, i))
            i += 2
            m = re.match(r"\[[^\]]*\]", src[i:b])  # row spacing like \\[-3pt]
            if m:
                i += m.end()
            last = i
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append((last, i))
            last = i + 1
        i += 1
    parts.append((last, b))
    return parts


def _trim(src, mask, a: int, b: int) -> tuple[int, int]:
    while a < b and (src[a].isspace() or mask[a]):
        a += 1
    while b > a and (src[b - 1].isspace() or mask[b - 1]):
        b -= 1
    return a, b


def _innermost_text(src, mask, a: int, b: int) -> tuple[int, int]:
    """For a cell like "\\hfill {\\small Vijayawada}" or "\\underline{\\small Intern}", the span of the words."""
    a, b = _trim(src, mask, a, b)
    for _ in range(4):
        m = re.match(
            r"(?:\\(?:hfill|small|footnotesize|normalsize|large|itshape|bfseries|scshape|centering|quad)\s*)+", src[a:b]
        )
        if m:
            a += m.end()
            a, b = _trim(src, mask, a, b)
        m = re.match(r"\\(?:textbf|textit|emph|underline|textsc|small|footnotesize|mbox)\s*\{", src[a:b]) or re.match(
            r"\{", src[a:b]
        )
        if m:
            ob = a + m.end() - 1
            cb = match_brace(src, ob, mask)
            if cb == b - 1:
                a, b = ob + 1, cb
                a, b = _trim(src, mask, a, b)
                continue
        break
    return a, b


def _tabular_entries(src, mask, calls, start, end, kind, items) -> list[dict]:
    entries = []
    for begin, body_start, body_end, env_end in _envs(src, mask, calls, start, end):
        rows = _split_top(src, mask, body_start, body_end, "\\\\")
        head_cells: list[list[tuple[int, int]]] = []
        own_items = [it for it in items if body_start <= it["start"] < body_end]
        for r0, r1 in rows:
            if "\\multicolumn" in src[r0:r1] and ("\\item" in src[r0:r1] or "itemize" in src[r0:r1]):
                break
            cells = [(_trim(src, mask, x0, x1)) for x0, x1 in _split_top(src, mask, r0, r1, "&")]
            if any(to_plain(src[x0:x1]) for x0, x1 in cells):
                head_cells.append(cells)
        if not head_cells:
            continue
        if kind == "education" and not own_items and len(head_cells) > 1:
            return []  # a table of education rows: recorded as a table and left as it is
        fields, slots = {}, {}
        first = head_cells[0]
        pa, pb = _innermost_text(src, mask, *first[0])
        bold = re.search(r"\\textbf\s*\{", src[first[0][0] : first[0][1]])
        fields["primary"] = to_plain(src[pa:pb])
        slots["primary"] = [pa, pb]
        if len(first) > 1:
            ra, rb = first[-1]
            right = src[ra:rb]
            for c in iter_macros(src, ra, rb, mask):
                if c.name == "href" and len(c.args) >= 2:
                    fields["url"] = c.args[0].inner(src).strip()
                    slots["url"] = [c.args[0].start + 1, c.args[0].end]
                    slots["link_call"] = [c.start, c.args[1].end + 1]
                    break
            if "url" not in fields and to_plain(right):
                da, db = _innermost_text(src, mask, ra, rb)
                fields["dates"] = to_plain(src[da:db])
                slots["dates"] = [da, db]
        if len(head_cells) > 1:
            second = head_cells[1]
            sa, sb = _innermost_text(src, mask, *second[0])
            fields["secondary"] = to_plain(src[sa:sb])
            slots["secondary"] = [sa, sb]
            if len(second) > 1:
                la, lb = _innermost_text(src, mask, *second[-1])
                fields["location"] = to_plain(src[la:lb])
                slots["location"] = [la, lb]
        fields["short_name"] = re.split(r"\s+[—–-]{1,2}\s+|\s*\|\s*", fields["primary"], maxsplit=1)[0].strip()
        if not bold and not own_items:
            continue
        entries.append(
            {
                "span": [begin.start, env_end],
                "heading": {
                    "macro": "tabular",
                    "span": [begin.start, own_items[0]["start"] if own_items else env_end],
                    "args": [],
                },
                "items": [_item_dict(src, it) for it in own_items],
                "items_region": [own_items[0]["start"], own_items[-1]["end"]] if own_items else None,
                "item_separator": _item_separator(src, own_items) if own_items else "",
                "fields": fields,
                "slots": slots,
            }
        )
    return entries


def _table_rows(src, mask, calls, start, end) -> list[list[str]]:
    rows = []
    for _, body_start, body_end, _ in _envs(src, mask, calls, start, end):
        for r0, r1 in _split_top(src, mask, body_start, body_end, "\\\\"):
            cells = [to_plain(src[a:b]) for a, b in _split_top(src, mask, r0, r1, "&")]
            if any(cells):
                rows.append(cells)
    return rows


def _tabular_skills(src, mask, calls, start, end) -> list[dict]:
    cats = []
    for _, body_start, body_end, _ in _envs(src, mask, calls, start, end):
        for r0, r1 in _split_top(src, mask, body_start, body_end, "\\\\"):
            cells = _split_top(src, mask, r0, r1, "&")
            if len(cells) < 2:
                continue
            name = to_plain(src[cells[0][0] : cells[0][1]]).strip().rstrip(":")
            va, vb = _trim(src, mask, *cells[-1])
            value = to_plain(src[va:vb])
            if not name or not value or len(name) > 60:
                continue
            cats.append(
                {
                    "name": name,
                    "span": [va, vb],
                    "items": [x.strip() for x in _split_top_level_commas(value) if x.strip()],
                }
            )
    return cats


def _item_separator(src, items) -> str:
    if len(items) >= 2:
        return src[items[0]["end"] : items[1]["start"]]
    line_start = src.rfind("\n", 0, items[0]["start"]) + 1
    return "\n" + re.match(r"[ \t]*", src[line_start:]).group()


def _find_headings(calls: list[MacroCall], items: list[dict], kind: str) -> list[MacroCall]:
    item_starts = {it["start"] for it in items}
    candidates = [
        c
        for c in calls
        if c.args
        and c.name not in FORMATTING
        and c.start not in item_starts
        and not _is_section_macro(c.name)
        and not any(it["start"] < c.start < it["end"] for it in items)
    ]
    if not candidates:
        return []
    named = [c for c in candidates if re.search(r"heading|entry|subhead|project|job|school|experience", c.name, re.I)]
    pool = named or [c for c in candidates if len(c.args) >= 2]
    if not pool:
        return []
    top = Counter(c.name for c in pool).most_common()
    names = {n for n, _ in top}
    # nested calls (a macro used inside another heading's args) are not headings
    heads = [c for c in candidates if c.name in names]
    out = []
    for c in heads:
        if any(o.start < c.start < o.end for o in out):
            continue
        out.append(c)
    return out


def _closing_for(src, mask, calls, after: int, first_item_start: int, limit: int) -> tuple[str, str] | None:
    """Detects how the item list after a heading is opened, so its closer can be found."""
    for c in calls:
        if c.start < after or c.start >= first_item_start:
            continue
        if c.name == "begin" and c.args:
            env = c.args[0].inner(src)
            if env in ("itemize", "enumerate", "description"):
                return ("env", env)
        if not c.args and c.name.endswith("Start"):
            return ("macro", c.name[: -len("Start")] + "End")
    return None


def _find_closer(src, mask, calls, how: tuple[str, str], after: int, limit: int) -> int:
    kind, name = how
    if kind == "macro":
        for c in calls:
            if c.start >= after and c.name == name and c.start < limit:
                return c.end if not c.args else c.args[-1].end + 1
        return -1
    depth = 0
    for c in calls:
        if c.start < after or c.start >= limit:
            continue
        if c.name == "begin" and c.args and c.args[0].inner(src) == name:
            depth += 1
        elif c.name == "end" and c.args and c.args[0].inner(src) == name:
            if depth == 0:
                return c.args[0].end + 1
            depth -= 1
    return -1


def _build_entries(src, mask, calls, headings, items, section_end, kind) -> list[dict]:
    entries = []
    for i, h in enumerate(headings):
        next_start = headings[i + 1].start if i + 1 < len(headings) else section_end
        own_items = [it for it in items if h.end <= it["start"] < next_start]
        end = h.end
        items_region = None
        if own_items:
            items_region = [own_items[0]["start"], own_items[-1]["end"]]
            end = own_items[-1]["end"]
            how = _closing_for(src, mask, calls, h.end, own_items[0]["start"], next_start)
            if how:
                closer = _find_closer(src, mask, calls, how, end, next_start)
                if closer > 0:
                    end = closer
        entry = {
            "span": [h.start, end],
            "heading": {
                "macro": h.name,
                "span": [h.start, h.end],
                "args": [{"inner": [a.start + 1, a.end], "text": to_plain(a.inner(src))} for a in h.args],
            },
            "items": [_item_dict(src, it) for it in own_items],
            "items_region": items_region,
            "item_separator": _item_separator(src, own_items) if own_items else "",
        }
        entry["fields"], entry["slots"] = _heading_fields(src, mask, h.args, kind)
        entries.append(entry)
    return entries


def _itemize_blocks(src, mask, calls, start, end) -> list[tuple[int, int]]:
    blocks = []
    depth, open_at = 0, -1
    for c in calls:
        if c.name == "begin" and c.args and c.args[0].inner(src) in ("itemize", "enumerate"):
            if depth == 0:
                open_at = c.start
            depth += 1
        elif c.name == "end" and c.args and c.args[0].inner(src) in ("itemize", "enumerate"):
            depth -= 1
            if depth == 0 and open_at >= 0:
                blocks.append((open_at, c.args[0].end + 1))
    return blocks


def _block_entries(src, mask, blocks, items, start, kind) -> list[dict]:
    entries = []
    prev_end = start
    for b_start, b_end in blocks:
        h_start = prev_end
        while h_start < b_start and (src[h_start].isspace() or mask[h_start]):
            h_start += 1
        heading_raw = src[h_start:b_start]
        if not to_plain(heading_raw).strip():
            prev_end = b_end
            continue
        own_items = [it for it in items if b_start <= it["start"] < b_end]
        # heading "args" are the \textbf / \textit groups and hfill-separated fragments
        args = []
        for c in iter_macros(src, h_start, b_start, mask):
            if c.name in ("textbf", "textit", "emph", "textsc") and c.args:
                a = c.args[0]
                args.append(Arg(a.start, a.end))
        fields, slots = _heading_fields(src, mask, args, kind, raw_heading=(h_start, b_start))
        entries.append(
            {
                "span": [h_start, b_end],
                "heading": {
                    "macro": "",
                    "span": [h_start, b_start],
                    "args": [{"inner": [a.start + 1, a.end], "text": to_plain(a.inner(src))} for a in args],
                },
                "items": [_item_dict(src, it) for it in own_items],
                "items_region": [own_items[0]["start"], own_items[-1]["end"]] if own_items else None,
                "item_separator": _item_separator(src, own_items) if own_items else "",
                "fields": fields,
                "slots": slots,
            }
        )
        prev_end = b_end
    return entries


def _heading_fields(src, mask, args: list[Arg], kind: str, raw_heading=None) -> tuple[dict, dict]:
    """Assigns meaning (name, title, dates, location, link, tech) to a heading's parts, and records the source
    span of each so a new entry can be cloned from this one."""
    fields: dict = {}
    slots: dict = {}
    remaining: list[Arg] = []
    for a in args:
        text = to_plain(a.inner(src))
        if not text and not find_hrefs(a.inner(src)):
            continue
        if "dates" not in fields and DATE_RX.search(text) and len(text) <= 40 and not _has_bold(src, a):
            fields["dates"] = text
            slots["dates"] = [a.start + 1, a.end]
        else:
            remaining.append(a)

    region = raw_heading or ((args[0].start, args[-1].end + 1) if args else None)
    if region:
        for c in iter_macros(src, region[0], region[1], mask):
            if c.name == "href" and len(c.args) >= 2 and "url" not in fields:
                url = c.args[0].inner(src).strip()
                if not url.startswith("mailto:"):
                    fields["url"] = url
                    slots["url"] = [c.args[0].start + 1, c.args[0].end]
                    slots["link_call"] = [c.start, c.args[1].end + 1]
            if c.name in ("emph", "textit") and c.args and kind == "projects" and "tech" not in fields:
                t = to_plain(c.args[0].inner(src))
                if t and "," in t or (t and len(t.split()) <= 8 and kind == "projects" and len(remaining) <= 1):
                    fields["tech"] = t
                    slots["tech"] = [c.args[0].start + 1, c.args[0].end]

    primary_done = False
    roles = ["secondary", "location"]
    for a in remaining:
        text = to_plain(a.inner(src))
        if not text:
            continue
        if not primary_done:
            bold = _first_macro_arg(src, mask, a, ("textbf",))
            if bold is not None and to_plain(bold.inner(src)):
                fields["primary"] = to_plain(bold.inner(src))
                slots["primary"] = [bold.start + 1, bold.end]
            else:
                fields["primary"] = text.split("|")[0].strip()
                slots["primary"] = (
                    [a.start + 1, a.end] if "|" not in text else slots.get("primary", [a.start + 1, a.end])
                )
            primary_done = True
            continue
        if "tech" in slots and a.start < slots["tech"][0] < a.end:
            continue
        if roles:
            role = roles.pop(0)
            fields[role] = text
            slots[role] = [a.start + 1, a.end]

    if kind == "education" and "secondary" in fields:
        m = GRADE_RX.search(fields["secondary"])
        if m:
            fields["grade"] = m.group(1).strip()
            fields["secondary"] = fields["secondary"][: m.start()].strip(" —–-|,")
    if "primary" in fields:
        # "Resolve — Intelligent Issue Resolution Platform": the short name is the part before the dash
        parts = re.split(r"\s+[—–-]{1,2}\s+|\s*\|\s*", fields["primary"], maxsplit=1)
        fields["short_name"] = parts[0].strip()
    return fields, slots


def _has_bold(src, a: Arg) -> bool:
    return "\\textbf" in a.inner(src)


def _first_macro_arg(src, mask, a: Arg, names: tuple[str, ...]) -> Arg | None:
    for c in iter_macros(src, a.start + 1, a.end, mask):
        if c.name in names and c.args:
            return c.args[0]
    return None


def _entry_separator(src, entries) -> str:
    if len(entries) >= 2:
        return src[entries[0]["span"][1] : entries[1]["span"][0]]
    start = entries[0]["span"][0]
    line_start = src.rfind("\n", 0, start) + 1
    return "\n" + re.match(r"[ \t]*", src[line_start:]).group()


def _parse_skill_lines(src, mask, calls, start, end) -> list[dict]:
    cats = []
    bolds = [c for c in calls if c.name == "textbf" and c.args]
    for idx, c in enumerate(bolds):
        name_raw = c.args[0].inner(src)
        name = to_plain(name_raw).strip()
        if not name or len(name) > 60:
            continue
        pos = c.args[0].end + 1
        limit = bolds[idx + 1].start if idx + 1 < len(bolds) else end
        j = pos
        while j < limit and src[j] in " \t":
            j += 1
        if j < limit and src[j] == "{":
            close = match_brace(src, j, mask)
            if close < 0:
                continue
            v_start, v_end = j + 1, close
        else:
            v_start = j
            stop = limit
            for pat in ("\\\\", "\n\n"):
                k = src.find(pat, v_start, limit)
                if k >= 0:
                    stop = min(stop, k)
            # stop at an unmatched closing brace (end of an enclosing group)
            depth = 0
            for k in range(v_start, stop):
                if mask[k]:
                    continue
                if src[k] == "{" and src[k - 1] != "\\":
                    depth += 1
                elif src[k] == "}" and src[k - 1] != "\\":
                    if depth == 0:
                        stop = k
                        break
                    depth -= 1
            v_end = stop
        # skip leading ": " inside the value; keep it as part of the template
        m = re.match(r"\s*:?\s*", src[v_start:v_end])
        if m:
            v_start += m.end()
        while v_end > v_start and src[v_end - 1] in " \t\n":
            v_end -= 1
        value = to_plain(src[v_start:v_end])
        name = name.rstrip(":").strip()
        if not value:
            continue
        items = [s.strip() for s in _split_top_level_commas(value) if s.strip()]
        cats.append({"name": name, "span": [v_start, v_end], "items": items})
    return cats


def _split_top_level_commas(text: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _paragraph_span(src, mask, start, end) -> list[int] | None:
    i, j = start, end
    while i < j and (src[i].isspace() or mask[i]):
        i += 1
    while j > i and (src[j - 1].isspace() or mask[j - 1]):
        j -= 1
    if i >= j:
        return None
    # leave trailing/leading layout commands (\vspace{-5pt}, \medskip ...) outside the editable text
    layout_cmd = re.compile(
        r"\\(?:vspace\*?\{[^{}]*\}|hspace\*?\{[^{}]*\}|medskip|smallskip|bigskip|par|noindent|"
        r"newline)\s*$"
    )
    while True:
        m = layout_cmd.search(src[i:j])
        if not m:
            break
        j = i + m.start()
        while j > i and (src[j - 1].isspace() or mask[j - 1]):
            j -= 1
    lead_cmd = re.compile(r"^\s*\\(?:vspace\*?\{[^{}]*\}|medskip|smallskip|noindent)\s*")
    m = lead_cmd.match(src[i:j])
    if m:
        i += m.end()
    if i >= j:
        return None
    seg = src[i:j]
    # unwrap a single enclosing group such as "{\small ...}" or "\small{...}"
    m = re.match(r"^(\\[A-Za-z]+\s*)?\{", seg)
    if m:
        ob = i + m.end() - 1
        cb = match_brace(src, ob, mask)
        if cb == j - 1:
            i, j = ob + 1, cb
            m2 = re.match(r"\s*(\\(?:small|footnotesize|normalsize|large)\b\s*)?", src[i:j])
            if m2:
                i += m2.end()
    if not to_plain(src[i:j]):
        return None
    return [i, j]


def summarize_structure(structure: dict) -> list[dict]:
    """A compact view of the parsed template for the UI."""
    out = []
    for s in structure.get("sections", []):
        layout = s.get("layout")
        count = len(s.get("entries", []) or s.get("items", []) or s.get("skills", []))
        out.append(
            {
                "index": s["index"],
                "title": s["title"],
                "kind": s["kind"],
                "layout": layout,
                "count": count if layout != "paragraph" else 1,
            }
        )
    return out


__all__ = ["TemplateParseError", "classify_section", "parse_latex_template", "summarize_structure", "read_args"]
