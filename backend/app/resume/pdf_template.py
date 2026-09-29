"""PDF resumes as templates.

A PDF has no editable structure, so CareerPilot measures it and rebuilds it:

1. analyse    - every text span (font, size, weight, position), rules, links and icons are read with PyMuPDF.
                Scanned PDFs are read with OCR when Tesseract is available.
2. fingerprint - page size, margins, columns, fonts and sizes, heading style (weight, small caps, rule), entry
                layout (left/right aligned parts), bullet glyph and indents, line spacing, header layout.
3. reconstruct - a LaTeX template is generated from the fingerprint using the resume's own content. Its layout
                definitions are locked: tailoring only changes content, exactly as with an uploaded .tex file.
4. compare    - the reconstruction is compiled and measured against the original (heading positions, margins,
                page count, text) so the user sees how closely it matches before using it.
"""

import io
import re
import shutil
import statistics
from dataclasses import dataclass, field

import pymupdf

from app.resume.latex_syntax import escape, escape_url
from app.resume.latex_template import DATE_RX, GRADE_RX, classify_section

BULLETS = set("•◦▪▫●○■□–-*·‣⁃")
ICON_FONTS = re.compile(r"fontawesome|awesome|icons?|symbol|wingding|dingbat|zapf|glyph", re.I)


class PdfTemplateError(ValueError):
    pass


# --- reading ----------------------------------------------------------------------------------------


@dataclass
class Seg:
    x0: float
    x1: float
    text: str
    size: float
    font: str
    bold: bool
    italic: bool
    caps: bool
    icon: bool = False


@dataclass
class Row:
    page: int
    y0: float
    y1: float
    segs: list[Seg]
    bullet: bool = False
    text_x0: float = 0.0
    link_urls: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "  ".join(s.text for s in self.segs if not s.icon).strip()

    @property
    def left(self) -> str:
        return self.segs[0].text if self.segs else ""


def _is_bold(font: str, flags: int) -> bool:
    return bool(flags & 16) or bool(re.search(r"bold|black|heavy|semibold|demi|cmbx|bx\d|-bd|\bbd\b", font, re.I))


def _is_italic(font: str, flags: int) -> bool:
    return bool(flags & 2) or bool(re.search(r"italic|oblique|cmti|cmsl|\bit\b|-it", font, re.I))


def _is_caps(font: str) -> bool:
    return bool(re.search(r"csc|smallcap|caps|\bsc\b", font, re.I))


def _spans(page) -> list[dict]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    out.append(s)
    return out


def read_rows(doc) -> tuple[list[Row], list[dict], dict]:
    """Groups spans into visual rows (same baseline), split into segments at large horizontal gaps."""
    rows: list[Row] = []
    rules: list[dict] = []
    info = {"ocr": False}
    for pno, page in enumerate(doc):
        spans = _spans(page)
        if not spans:
            spans = _ocr_spans(page)
            info["ocr"] = info["ocr"] or bool(spans)
        spans.sort(key=lambda s: (round(s["origin"][1] / 2.0), s["bbox"][0]))
        links = [(pymupdf.Rect(lk["from"]), lk.get("uri", "")) for lk in page.get_links() if lk.get("uri")]
        groups: list[list[dict]] = []
        for s in spans:
            if groups and abs(groups[-1][0]["origin"][1] - s["origin"][1]) <= 2.2:
                groups[-1].append(s)
            else:
                groups.append([s])
        for g in groups:
            g.sort(key=lambda s: s["bbox"][0])
            segs: list[Seg] = []
            for s in g:
                icon = bool(ICON_FONTS.search(s["font"])) or (
                    len(s["text"].strip()) == 1
                    and ord(s["text"].strip()[0]) > 0x2400
                    and s["text"].strip() not in BULLETS
                )
                gap = s["bbox"][0] - segs[-1].x1 if segs else 0
                size = round(s["size"], 1)
                # a dash or dot is a bullet only as the first glyph of a line ("Jan 2026 – Mar 2026" is not)
                is_bullet = s["text"].strip() in BULLETS and not segs
                prev_is_bullet = len(segs) == 1 and segs[0].text in BULLETS
                if (
                    segs
                    and gap < max(12.0, size * 1.4)
                    and not icon
                    and not segs[-1].icon
                    and not is_bullet
                    and not prev_is_bullet
                ):
                    joiner = " " if gap > size * 0.15 else ""
                    segs[-1].text += joiner + s["text"].strip()
                    segs[-1].x1 = s["bbox"][2]
                else:
                    segs.append(
                        Seg(
                            s["bbox"][0],
                            s["bbox"][2],
                            s["text"].strip(),
                            size,
                            s["font"],
                            _is_bold(s["font"], s["flags"]),
                            _is_italic(s["font"], s["flags"]),
                            _is_caps(s["font"]),
                            icon,
                        )
                    )
            y0 = min(s["bbox"][1] for s in g)
            y1 = max(s["bbox"][3] for s in g)
            row = Row(pno, y0, y1, segs)
            rect = pymupdf.Rect(min(s.x0 for s in segs), y0, max(s.x1 for s in segs), y1)
            row.link_urls = [u for r, u in links if r.intersects(rect)]
            if segs and segs[0].text in BULLETS and len(segs) > 1:
                row.bullet = True
                row.segs = segs[1:]
                row.text_x0 = segs[1].x0
                row.bullet_x0 = segs[0].x0  # type: ignore[attr-defined]
                row.bullet_size = segs[0].size  # type: ignore[attr-defined]
                row.bullet_char = segs[0].text  # type: ignore[attr-defined]
            elif segs and segs[0].text[:1] in BULLETS and len(segs[0].text) > 2 and segs[0].text[1] == " ":
                row.bullet = True
                row.bullet_char = segs[0].text[0]  # type: ignore[attr-defined]
                row.bullet_x0 = segs[0].x0  # type: ignore[attr-defined]
                row.bullet_size = segs[0].size  # type: ignore[attr-defined]
                segs[0].text = segs[0].text[2:].strip()
                row.text_x0 = segs[0].x0
            else:
                row.text_x0 = segs[0].x0 if segs else 0
            rows.append(row)
        for d in page.get_drawings():
            r = d["rect"]
            if r.height <= 2.5 and r.width > page.rect.width * 0.4:
                rules.append({"page": pno, "y": r.y0, "x0": r.x0, "x1": r.x1, "width": d.get("width") or r.height})
    return rows, rules, info


def _ocr_spans(page) -> list[dict]:
    if not shutil.which("tesseract"):
        return []
    try:
        tp = page.get_textpage_ocr(dpi=200, full=True)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for block in page.get_text("dict", textpage=tp)["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    out.append(s)
    return out


# --- understanding ----------------------------------------------------------------------------------


def _mode(values: list[float], default: float) -> float:
    vals = [round(v * 2) / 2 for v in values if v]
    return statistics.mode(vals) if vals else default


def analyse(data: bytes) -> dict:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfTemplateError("That file could not be opened as a PDF.") from exc
    if doc.needs_pass:
        raise PdfTemplateError("The PDF is password-protected.")
    if len(doc) > 4:
        raise PdfTemplateError("The PDF has more than 4 pages; upload a resume, not a longer document.")
    rows, rules, info = read_rows(doc)
    if not rows:
        raise PdfTemplateError(
            "No text could be read from this PDF (it looks scanned) and OCR is not available on this server. "
            "Upload the original .docx or .tex, or a PDF exported from a document editor."
        )
    page = doc[0].rect
    all_sizes = [s.size for r in rows for s in r.segs if not s.icon for _ in range(max(1, len(s.text) // 8))]
    body_size = _mode(all_sizes, 10.0)

    # name: the largest text near the top of page 1
    first_rows = [r for r in rows if r.page == 0][:6]
    name_row = max(first_rows, key=lambda r: max((s.size for s in r.segs), default=0))

    # headings: short, styled differently from body text, recognisable as sections, usually over a rule
    headings = []
    for i, r in enumerate(rows):
        if r is name_row or r.bullet or len(r.segs) != 1 or len(r.text) > 45:
            continue
        seg = r.segs[0]
        styled = (
            seg.size >= body_size + 1.2
            or seg.caps
            or (seg.bold and seg.size >= body_size)
            or (r.text.isupper() and len(r.text) > 3)
        )
        if re.search(r":\s*\S", r.text):
            continue  # "Tools: Git, Docker" is a skills line, not a heading
        ruled = any(rule["page"] == r.page and 0 <= rule["y"] - r.y1 <= 14 for rule in rules)
        known = classify_section(r.text) != "other"
        if styled and (known or ruled) and len(r.text.split()) <= 6:
            headings.append((i, ruled))
    if sum(1 for _, ruled in headings) >= 2:
        headings = [(i, ruled) for i, ruled in headings if ruled]  # the document rules its headings: trust that
    headings = [i for i, _ in headings]
    if not headings:
        raise PdfTemplateError("No section headings were recognised in this PDF.")

    left_margin = min(r.text_x0 if not r.bullet else getattr(r, "bullet_x0", r.text_x0) for r in rows)
    right_edge = max(s.x1 for r in rows for s in r.segs)
    columns = _detect_columns(rows, headings, page.width, left_margin)

    header_rows = [r for r in rows[: headings[0]] if r is not name_row]
    sections = []
    for n, hi in enumerate(headings):
        end = headings[n + 1] if n + 1 < len(headings) else len(rows)
        title = _clean_title(rows[hi].text)
        sec_rows = rows[hi + 1 : end]
        sections.append(_section(title, sec_rows, left_margin, right_edge, body_size))

    heading_rows = [rows[i] for i in headings]
    h_seg = heading_rows[0].segs[0]
    rule_below = any(
        any(rule["page"] == h.page and 0 <= rule["y"] - h.y1 <= 14 for rule in rules) for h in heading_rows
    )
    rule_w = statistics.median([r["width"] for r in rules]) if rules else 0.4
    bullets = [r for r in rows if r.bullet]
    leading = _leading(rows, body_size)

    fp = {
        "page": {"width": round(page.width, 1), "height": round(page.height, 1), "count": len(doc)},
        "margins": {
            "left": round(left_margin, 1),
            "right": round(page.width - right_edge, 1),
            "top": round(name_row.y0, 1),
        },
        "columns": columns,
        "fonts": _font_family(rows),
        "body": {"size": body_size, "leading": round(leading, 2)},
        "name": {
            "size": max(s.size for s in name_row.segs),
            "caps": any(s.caps for s in name_row.segs),
            "bold": any(s.bold for s in name_row.segs),
            "align": _align(name_row, page.width, left_margin),
            "text": " ".join(s.text for s in name_row.segs),
        },
        "heading": {
            "size": h_seg.size,
            "bold": h_seg.bold,
            "caps": h_seg.caps,
            "upper": all(_clean_title(rows[i].text).isupper() for i in headings),
            "rule": rule_below,
            "rule_width": round(rule_w, 2),
            "gap_to_rule": round(
                statistics.median(
                    [
                        min(
                            (
                                rule["y"] - h.y1
                                for rule in rules
                                if rule["page"] == h.page and 0 <= rule["y"] - h.y1 <= 14
                            ),
                            default=3,
                        )
                        for h in heading_rows
                    ]
                ),
                1,
            ),
        },
        "bullet": {
            "char": _mode_str([getattr(r, "bullet_char", "•") for r in bullets], "•"),
            "size": _mode([getattr(r, "bullet_size", body_size) for r in bullets], body_size),
            "indent": round(_mode([getattr(r, "bullet_x0", left_margin) - left_margin for r in bullets], 10), 1),
            "text_indent": round(_mode([r.text_x0 - left_margin for r in bullets], 18), 1),
        }
        if bullets
        else None,
        "header": _header(header_rows, page.width, left_margin),
        "section_order": [s["title"] for s in sections],
        "text_selectable": not info["ocr"],
        "ocr": info["ocr"],
    }
    positions = [
        {"title": _clean_title(rows[i].text), "page": rows[i].page, "y": round(rows[i].y0, 1)} for i in headings
    ]
    return {
        "fingerprint": fp,
        "sections": sections,
        "heading_positions": positions,
        "header_rows": [_row_dict(r) for r in header_rows],
        "name": fp["name"]["text"],
        "text": "\n".join(p.get_text() for p in doc),
    }


def _mode_str(values: list[str], default: str) -> str:
    return statistics.mode(values) if values else default


def _clean_title(t: str) -> str:
    t = re.sub(r"\b([A-Z])\s(?=[a-z]{2,})", r"\1", t)  # "W ork" -> "Work" (kerning split)
    return re.sub(r"\s+", " ", t).strip(" :")


def _row_dict(r: Row) -> dict:
    return {"text": r.text, "links": r.link_urls, "icons": sum(1 for s in r.segs if s.icon)}


def _align(r: Row, width: float, left: float) -> str:
    x0 = min(s.x0 for s in r.segs)
    x1 = max(s.x1 for s in r.segs)
    return "center" if abs((x0 + x1) / 2 - width / 2) < 12 and x0 > left + 20 else "left"


def _leading(rows: list[Row], size: float) -> float:
    gaps = []
    for a, b in zip(rows, rows[1:], strict=False):
        if a.page == b.page and b.bullet is False and a.bullet and abs(b.text_x0 - a.text_x0) < 2:
            gaps.append(b.y0 - a.y0)  # continuation line of a bullet: pure line spacing
    return statistics.median(gaps) if gaps else size * 1.25


def _detect_columns(rows: list[Row], headings: list[int], width: float, left: float) -> dict:
    starts = [rows[i].segs[0].x0 for i in headings]
    mid = width * 0.3
    right_col = [x for x in starts if x > left + mid]
    if right_col and len(right_col) < len(starts):
        return {"count": 2, "split_x": round(min(right_col), 1)}
    return {"count": 1}


def _font_family(rows: list[Row]) -> dict:
    names = [s.font for r in rows for s in r.segs if not s.icon]
    base = statistics.mode([re.sub(r"^[A-Z]{6}\+", "", n) for n in names]) if names else ""
    low = base.lower()
    family = "cm"
    for key, fam in (
        ("cmr", "cm"),
        ("cmbx", "cm"),
        ("lmroman", "cm"),
        ("lmsans", "lmsans"),
        ("times", "times"),
        ("nimbusrom", "times"),
        ("termes", "times"),
        ("helvetica", "helvet"),
        ("arial", "helvet"),
        ("nimbussan", "helvet"),
        ("liberationsans", "helvet"),
        ("calibri", "carlito"),
        ("carlito", "carlito"),
        ("cambria", "caladea"),
        ("garamond", "garamond"),
        ("palatino", "palatino"),
        ("pagella", "palatino"),
        ("palladio", "palatino"),
        ("charter", "charter"),
        ("roboto", "roboto"),
        ("lato", "lato"),
        ("opensans", "opensans"),
        ("sourcesans", "sourcesans"),
        ("fira", "fira"),
        ("georgia", "times"),
        ("verdana", "helvet"),
        ("tahoma", "helvet"),
        ("segoe", "helvet"),
    ):
        if key in low.replace(" ", "").replace("-", ""):
            family = fam
            break
    else:
        family = "helvet" if re.search(r"sans|grotesk|gothic", low) else "cm"
    return {"detected": base, "family": family}


def _header(rows: list[Row], width: float, left: float) -> dict:
    items = []
    for r in rows:
        for s in r.segs:
            if s.icon:
                continue
            items.append(s.text)
    links = [u for r in rows for u in r.link_urls]
    seps = [c for r in rows for s in r.segs for c in s.text if c in "|•·◦"]
    return {
        "align": _align(rows[0], width, left) if rows else "center",
        "items": items,
        "links": links,
        "icons": any(s.icon for r in rows for s in r.segs),
        "separator": seps[0] if seps else "",
        "size": _mode([s.size for r in rows for s in r.segs if not s.icon], 10),
    }


def _section(title: str, rows: list[Row], left: float, right: float, body: float) -> dict:
    kind = classify_section(title)
    skills_indent = 0.0
    entries: list[dict] = []
    items: list[str] = []
    skills: list[dict] = []
    paragraph: list[str] = []
    for r in rows:
        if r.bullet:
            target = entries[-1]["bullets"] if entries else items
            target.append(r.text)
            continue
        if (
            (items or (entries and entries[-1]["bullets"]))
            and not r.segs[0].bold
            and r.text_x0 > left + 4
            and not _right_part(r, right)
        ):
            # continuation of the previous bullet
            if entries and entries[-1]["bullets"]:
                entries[-1]["bullets"][-1] += " " + r.text
            elif items:
                items[-1] += " " + r.text
            continue
        m = re.match(r"^([^:]{2,40}):\s*(.+)$", r.text)
        if r.segs[0].bold and m and (kind == "skills" or r.segs[0].text.rstrip().endswith(":")):
            skills_indent = max(0.0, r.text_x0 - left)
            skills.append(
                {"name": m.group(1).strip(), "items": [x.strip() for x in re.split(r",\s*", m.group(2)) if x.strip()]}
            )
            continue
        right_txt = _right_part(r, right)
        left_txt = r.segs[0].text if right_txt else r.text
        if r.segs[0].bold and kind != "summary":
            e = {
                "primary": left_txt,
                "dates": right_txt,
                "secondary": "",
                "location": "",
                "bullets": [],
                "url": r.link_urls[0] if r.link_urls else "",
                "icon": any(s.icon for s in r.segs),
                "primary_italic": r.segs[0].italic,
                "right_bold": bool(right_txt) and r.segs[-1].bold,
            }
            entries.append(e)
            continue
        if entries and not entries[-1]["bullets"] and not entries[-1]["secondary"] and (r.segs[0].italic or right_txt):
            entries[-1]["secondary"] = left_txt
            entries[-1]["location"] = right_txt
            entries[-1]["secondary_italic"] = r.segs[0].italic
            continue
        if entries and entries[-1]["bullets"]:
            entries[-1]["bullets"][-1] += " " + r.text
        else:
            paragraph.append(r.text)
    if kind == "skills" and skills:
        layout = "skills"
    elif entries:
        layout = "entries"
    elif items:
        layout = "items"
    else:
        layout = "paragraph"
    for e in entries:
        if kind == "education" and e["secondary"]:
            g = GRADE_RX.search(e["secondary"])
            if g:
                e["grade"] = g.group(1)
        if not DATE_RX.search(e["dates"] or "") and e["dates"] and not e["location"]:
            pass
    return {
        "title": title,
        "kind": kind,
        "layout": layout,
        "entries": entries,
        "items": items,
        "skills": skills,
        "paragraph": " ".join(paragraph),
        "skills_indent": round(skills_indent, 1),
    }


def _right_part(r: Row, right: float) -> str:
    segs = [s for s in r.segs if not s.icon]
    if len(segs) >= 2 and segs[-1].x1 > right - 25 and segs[-1].x0 - segs[-2].x1 > 20:
        return segs[-1].text
    return ""


# --- reconstruction -------------------------------------------------------------------------------

FONT_PACKAGES = {
    "cm": "",
    "lmsans": "\\renewcommand{\\familydefault}{\\sfdefault}\n",
    "times": "\\usepackage{mathptmx}\n",
    "helvet": "\\usepackage[scaled]{helvet}\n\\renewcommand{\\familydefault}{\\sfdefault}\n",
    "carlito": "\\usepackage[sfdefault]{carlito}\n",
    "caladea": "\\usepackage{caladea}\n",
    "garamond": "\\usepackage{ebgaramond}\n",
    "palatino": "\\usepackage{mathpazo}\n",
    "charter": "\\usepackage{XCharter}\n",
    "roboto": "\\usepackage[sfdefault]{roboto}\n",
    "lato": "\\usepackage[default]{lato}\n",
    "opensans": "\\usepackage[default]{opensans}\n",
    "sourcesans": "\\usepackage[default]{sourcesanspro}\n",
    "fira": "\\usepackage[sfdefault]{FiraSans}\n",
}
ICON_FOR = {
    "email": "\\faEnvelope",
    "phone": "\\faPhone",
    "linkedin": "\\faLinkedin",
    "github": "\\faGithub",
    "portfolio": "\\faGlobe",
}


def _pt(v: float) -> str:
    return f"{max(0.0, v):.1f}pt"


def build_latex(a: dict, spacing: dict | None = None) -> str:
    """Generates a LaTeX template that reproduces the analysed PDF, filled with its own content."""
    fp = a["fingerprint"]
    sp = {"before": 6.0, "after": 3.0, "entry": 2.0, "item": 0.0, "top_adjust": 0.0, **(spacing or {})}
    m = fp["margins"]
    body = fp["body"]
    lead = body["leading"] if body["leading"] > body["size"] else body["size"] * 1.2
    h = fp["heading"]
    b = fp["bullet"] or {"char": "•", "size": body["size"], "indent": 10, "text_indent": 18}
    bullet_label = "$\\bullet$" if b["char"] in "•●" else ("--" if b["char"] in "–-" else escape(b["char"]))
    heading_style = (
        ("\\bfseries" if h["bold"] else "")
        + ("\\scshape" if h["caps"] else "")
        + ("\\MakeUppercase" if h["upper"] and not h["caps"] else "")
    )
    rule = f"[{{\\vspace{{{_pt(h['gap_to_rule'] - 1.5)}}}\\titlerule[{h['rule_width']}pt]}}]" if h["rule"] else ""
    lines = [
        "% Rebuilt from an uploaded PDF by CareerPilot. Layout definitions are locked; only content is tailored.",
        "\\documentclass[10pt]{article}",
        f"\\usepackage[paperwidth={_pt(fp['page']['width'])},paperheight={_pt(fp['page']['height'])},"
        f"left={_pt(m['left'])},right={_pt(m['right'])},top={_pt(m['top'] + sp['top_adjust'])},bottom={_pt(28)},"
        "nohead,nofoot]{geometry}",
        FONT_PACKAGES.get(fp["fonts"]["family"], "").rstrip(),
        "\\usepackage[T1]{fontenc}" if fp["fonts"]["family"] not in ("cm",) else "",
        "\\usepackage{titlesec}",
        "\\usepackage{enumitem}",
        "\\usepackage[hidelinks]{hyperref}",
        "\\usepackage{tabularx}",
        "\\usepackage{fontawesome5}"
        if fp["header"]["icons"] or any(e.get("icon") for s in a["sections"] for e in s["entries"])
        else "",
        "\\pagestyle{empty}",
        "\\setlength{\\parindent}{0pt}",
        "\\setlength{\\tabcolsep}{0pt}",
        "\\raggedbottom",
        "\\urlstyle{same}",
        f"\\newcommand{{\\bodyfont}}{{\\fontsize{{{body['size']}}}{{{lead:.2f}}}\\selectfont}}",
        f"\\titleformat{{\\section}}{{\\fontsize{{{h['size']}}}{{{h['size'] * 1.2:.1f}}}\\selectfont{heading_style}}}"
        f"{{}}{{0em}}{{}}{rule}",
        f"\\titlespacing*{{\\section}}{{0pt}}{{{_pt(sp['before'])}}}{{{_pt(sp['after'])}}}",
        "\\newcommand{\\resumeItem}[1]{\\item #1}",
        "\\newcommand{\\resumeSubheading}[4]{\\item\\begin{tabular*}{\\linewidth}[t]{l@{\\extracolsep{\\fill}}r}"
        "\\textbf{#1} & {\\entryDateStyle #2} \\\\ {\\entrySecondaryStyle #3} & {\\entrySecondaryStyle #4} \\\\"
        f"\\end{{tabular*}}\\vspace{{{_pt(sp['entry'])}}}}}",
        "\\newcommand{\\resumeProjectHeading}[2]{\\item\\begin{tabular*}{\\linewidth}{l@{\\extracolsep{\\fill}}r}"
        f"#1 & {{\\entryDateStyle #2}} \\\\\\end{{tabular*}}\\vspace{{{_pt(sp['entry'] - 1)}}}}}",
        "\\newcommand{\\resumeSubHeadingListStart}{\\begin{itemize}[leftmargin=0pt,label={},itemsep=0pt,"
        "topsep=0pt,parsep=0pt,partopsep=0pt]}",
        "\\newcommand{\\resumeSubHeadingListEnd}{\\end{itemize}}",
        f"\\newcommand{{\\resumeItemListStart}}{{\\begin{{itemize}}[leftmargin={_pt(b['text_indent'])},"
        f"labelsep={_pt(b['text_indent'] - b['indent'] - b['size'] * 0.55)},"
        f"label={{\\fontsize{{{b['size']}}}{{{b['size']}}}\\selectfont {bullet_label}}},"
        f"itemsep={_pt(sp['item'])},topsep=0pt,parsep=0pt,partopsep=0pt]}}",
        f"\\newcommand{{\\resumeItemListEnd}}{{\\end{{itemize}}\\vspace{{{_pt(sp['entry'])}}}}}",
        "\\newcommand{\\entryDateStyle}{" + ("\\bfseries" if _right_bold(a) else "") + "}",
        "\\newcommand{\\entrySecondaryStyle}{\\itshape}",
        "\\begin{document}",
        "\\bodyfont",
    ]
    lines += _header_tex(a)
    for s in a["sections"]:
        lines.append(f"\\section{{{escape(s['title'])}}}")
        lines += _section_tex(s)
    lines.append("\\end{document}")
    return "\n".join(x for x in lines if x != "") + "\n"


def _right_bold(a: dict) -> bool:
    flags = [e.get("right_bold") for s in a["sections"] for e in s["entries"] if e.get("dates")]
    return bool(flags) and sum(bool(f) for f in flags) >= len(flags) / 2


def _link_kind(url: str) -> str:
    u = url.lower()
    if u.startswith("mailto:"):
        return "email"
    if u.startswith("tel:"):
        return "phone"
    if "linkedin." in u:
        return "linkedin"
    if "github.com" in u:
        return "github"
    return "portfolio"


def _header_tex(a: dict) -> list[str]:
    fp = a["fingerprint"]
    hdr = fp["header"]
    name = fp["name"]
    style = ("\\bfseries" if name["bold"] else "") + ("\\scshape" if name["caps"] else "")
    env = "center" if name["align"] == "center" else "flushleft"
    out = [
        f"\\begin{{{env}}}",
        f"{{\\fontsize{{{name['size']}}}{{{name['size'] * 1.2:.1f}}}\\selectfont{style} "
        f"{escape(name['text'])}}}\\\\[3pt]",
    ]
    parts = []
    used_links = set()
    rows = a.get("header_rows", [])
    link_texts = {}
    for r in rows:
        for u in r["links"]:
            link_texts.setdefault(u, None)
    # contact items in their original order; items that were links keep their link
    items = hdr["items"]
    links = hdr["links"]
    for text in items:
        if text in ("|", "•", "·"):
            continue
        url = next((u for u in links if u not in used_links and (_matches(text, u))), "")
        kind = (
            _link_kind(url)
            if url
            else ("email" if "@" in text else "phone" if re.fullmatch(r"[+\d\s()-]{8,}", text) else "")
        )
        icon = (ICON_FOR.get(kind, "") + "\\ ") if hdr["icons"] and kind else ""
        if url:
            used_links.add(url)
            parts.append(f"\\href{{{escape_url(url)}}}{{{icon}\\underline{{{escape(text)}}}}}")
        else:
            parts.append(f"{icon}{escape(text)}")
    sep = (
        f" {escape(hdr['separator'])} "
        if hdr["separator"] and hdr["separator"] != "|"
        else (" $|$ " if hdr["separator"] == "|" else " \\quad ")
    )
    out.append(f"{{\\fontsize{{{hdr['size']}}}{{{hdr['size'] * 1.25:.1f}}}\\selectfont " + sep.join(parts) + "}")
    out.append(f"\\end{{{env}}}")
    out.append("\\vspace{-8pt}")
    return out


def _matches(text: str, url: str) -> bool:
    t = text.lower().replace(" ", "")
    u = url.lower()
    if "@" in t:
        return u.startswith("mailto:") and t in u
    for k in ("linkedin", "github", "portfolio", "website", "blog"):
        if k in t:
            return k in u or (k in ("portfolio", "website", "blog") and _link_kind(url) == "portfolio")
    return t.strip("/") in u


def _section_tex(s: dict) -> list[str]:
    out = []
    if s["layout"] == "paragraph":
        out.append(escape(s["paragraph"]))
    elif s["layout"] == "skills":
        out.append(
            f"\\begin{{itemize}}[leftmargin={_pt(s.get('skills_indent', 0))},label={{}},topsep=0pt,itemsep=0pt,"
            "parsep=0pt]\\item{"
        )
        out.append(
            " \\\\\n".join(f"\\textbf{{{escape(c['name'])}}}{{: {escape(', '.join(c['items']))}}}" for c in s["skills"])
        )
        out.append("}\\end{itemize}")
    elif s["layout"] == "items":
        out.append("\\resumeItemListStart")
        out += [f"  \\resumeItem{{{escape(t)}}}" for t in s["items"]]
        out.append("\\resumeItemListEnd")
    elif s["layout"] == "entries":
        out.append("\\resumeSubHeadingListStart")
        for e in s["entries"]:
            if s["kind"] == "projects" and not e["secondary"]:
                if e.get("url") and e.get("icon"):
                    title = f"\\textbf{{{escape(e['primary'])}}} \\href{{{escape_url(e['url'])}}}{{\\faGithub}}"
                elif e.get("url"):
                    title = f"\\href{{{escape_url(e['url'])}}}{{\\textbf{{{escape(e['primary'])}}}}}"
                else:
                    title = f"\\textbf{{{escape(e['primary'])}}}"
                out.append(f"  \\resumeProjectHeading{{{title}}}{{{escape(e['dates'])}}}")
            else:
                out.append(
                    f"  \\resumeSubheading{{{escape(e['primary'])}}}{{{escape(e['dates'])}}}"
                    f"{{{escape(e['secondary'])}}}{{{escape(e['location'])}}}"
                )
            if e["bullets"]:
                out.append("  \\resumeItemListStart")
                out += [f"    \\resumeItem{{{escape(t)}}}" for t in e["bullets"]]
                out.append("  \\resumeItemListEnd")
        out.append("\\resumeSubHeadingListEnd")
    return out


# --- comparison & calibration ----------------------------------------------------------------------


def heading_positions(pdf: bytes, titles: list[str]) -> list[dict]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out = []
    remaining = [t.lower() for t in titles]
    for pno, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                txt = _clean_title("".join(s["text"] for s in line["spans"]).strip())
                if remaining and txt.lower() == remaining[0]:
                    out.append({"title": txt, "page": pno, "y": round(line["bbox"][1], 1)})
                    remaining.pop(0)
    return out


def compare(original: dict, pdf: bytes) -> dict:
    """Measures how closely the reconstruction matches the uploaded PDF."""
    fp = original["fingerprint"]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    pos = heading_positions(pdf, [p["title"] for p in original["heading_positions"]])
    diffs = []
    for o, g in zip(original["heading_positions"], pos, strict=False):
        if o["page"] == g["page"]:
            diffs.append(abs(o["y"] - g["y"]))
        else:
            diffs.append(99.0)
    words_o = re.findall(r"[a-z0-9]+", original["text"].lower())
    words_g = re.findall(r"[a-z0-9]+", "\n".join(p.get_text() for p in doc).lower())
    import difflib

    text_match = difflib.SequenceMatcher(None, words_o, words_g, autojunk=False).ratio() if words_o else 0
    return {
        "pages_original": fp["page"]["count"],
        "pages_rebuilt": len(doc),
        "headings_found": len(pos),
        "headings_total": len(original["heading_positions"]),
        "max_heading_offset_pt": round(max(diffs), 1) if diffs else None,
        "mean_heading_offset_pt": round(sum(diffs) / len(diffs), 1) if diffs else None,
        "text_preserved": round(text_match * 100, 1),
    }


def calibrate(a: dict, compile_fn) -> tuple[str, bytes | None, dict, str]:
    """Builds the template and tunes vertical spacing so section headings land where they are in the original."""
    spacing = {"before": 6.0, "after": 3.0, "entry": 1.0, "item": 0.0, "top_adjust": 0.0}
    best = None
    for _ in range(8):
        src = build_latex(a, spacing)
        res = compile_fn(src)
        if res.pdf is None:
            return src, None, {}, res.error
        pos = heading_positions(res.pdf, [p["title"] for p in a["heading_positions"]])
        cmp = compare(a, res.pdf)
        score = cmp["mean_heading_offset_pt"] if cmp["mean_heading_offset_pt"] is not None else 999
        fits = cmp["pages_rebuilt"] <= cmp["pages_original"]
        if best is None or (fits and not best[3].get("fits")) or (fits == best[3].get("fits") and score < best[0]):
            cmp["fits"] = fits
            best = (score, src, res.pdf, cmp)
        if (score < 1.5 and fits) or len(pos) < 2:
            break
        if not fits:
            # the rebuilt page is taller than the original: tighten the flexible gaps a little
            spacing["before"] = max(0.0, spacing["before"] - 1.5)
            spacing["entry"] = max(-2.0, spacing["entry"] - 0.8)
            spacing["after"] = max(0.0, spacing["after"] - 0.8)
            continue
        orig = a["heading_positions"]
        same = [(o, g) for o, g in zip(orig, pos, strict=False) if o["page"] == g["page"] == 0]
        if len(same) >= 2:
            # first heading offset -> top margin; spread of later headings -> spacing between sections
            spacing["top_adjust"] += same[0][0]["y"] - same[0][1]["y"]
            drift = [(o["y"] - g["y"]) - (same[0][0]["y"] - same[0][1]["y"]) for o, g in same[1:]]
            per_section = statistics.mean(d / (i + 1) for i, d in enumerate(drift))
            spacing["before"] = max(0.0, spacing["before"] + per_section * 0.6)
            spacing["entry"] = max(-2.0, spacing["entry"] + per_section * 0.2)
    _, src, pdf, cmp = best
    return src, pdf, cmp, ""


def page_count(pdf: bytes) -> int:
    return len(pymupdf.open(stream=pdf, filetype="pdf"))


def render_page_png(data: bytes, page: int = 0, zoom: float = 1.3) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    return doc[page].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


__all__ = ["PdfTemplateError", "analyse", "build_latex", "calibrate", "compare", "io"]
