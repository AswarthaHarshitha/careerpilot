"""Word (.docx) resume templates.

Same contract as the LaTeX engine: parse the document into sections and entries, then write tailored content
back without touching formatting. Text is replaced inside existing runs (keeping their fonts, sizes and
bold/italic), and new entries or bullets are deep copies of paragraphs already in the document.

PDF export needs LibreOffice (``soffice``) on the server; without it the .docx is still produced.
"""

import copy
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from docx import Document
from docx.oxml.ns import qn

from app.core.config import get_settings
from app.resume.latex_template import DATE_RX, GRADE_RX, _split_top_level_commas, classify_section

MAX_UNCOMPRESSED = 30_000_000


class DocxTemplateError(ValueError):
    pass


def _open(data: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = sum(i.file_size for i in z.infolist())
            if total > MAX_UNCOMPRESSED:
                raise DocxTemplateError("The .docx expands to an unusually large size and was rejected.")
            names = set(z.namelist())
            if "word/document.xml" not in names:
                raise DocxTemplateError("That file is not a Word document.")
            if any(n.endswith("vbaProject.bin") for n in names):
                raise DocxTemplateError("Documents containing macros are not accepted.")
        return Document(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocxTemplateError("That file is not a valid .docx document.") from exc


def _paragraphs(doc) -> list:
    return list(doc.element.body.iter(qn("w:p")))


_T, _TAB, _BR = qn("w:t"), qn("w:tab"), qn("w:br")


def _text(p) -> str:
    parts = []
    for el in p.iter(_T, _TAB, _BR):
        parts.append(el.text or "" if el.tag == _T else ("\t" if el.tag == _TAB else "\n"))
    return "".join(parts).strip()


def _style(p) -> str:
    ps = p.find(qn("w:pPr"))
    if ps is None:
        return ""
    st = ps.find(qn("w:pStyle"))
    return st.get(qn("w:val"), "") if st is not None else ""


def _is_bullet(p) -> bool:
    ps = p.find(qn("w:pPr"))
    if ps is not None and ps.find(qn("w:numPr")) is not None:
        return True
    return "list" in _style(p).lower() or bool(re.match(r"^[•▪◦●\-–]\s", _text(p)))


def _is_bold(p) -> bool:
    runs = [r for r in p.iter(qn("w:r")) if "".join(t.text or "" for t in r.iter(qn("w:t"))).strip()]
    if not runs:
        return False
    for r in runs:
        rpr = r.find(qn("w:rPr"))
        b = rpr.find(qn("w:b")) if rpr is not None else None
        if b is None or b.get(qn("w:val")) in ("0", "false"):
            return False
    return True


def _is_section_heading(p) -> bool:
    text = _text(p)
    if not text or len(text) > 45:
        return False
    style = _style(p).lower()
    if style.startswith("heading") or style == "title":
        return classify_section(text) != "other" or style.startswith("heading")
    looks = text.isupper() or _is_bold(p)
    return looks and classify_section(text) != "other" and len(text.split()) <= 5


def parse_docx_template(data: bytes) -> dict:
    doc = _open(data)
    paras = _paragraphs(doc)
    heads = [i for i, p in enumerate(paras) if _is_section_heading(p)]
    if not heads:
        raise DocxTemplateError("No section headings (Experience, Projects, Skills...) were found in the document.")
    header_text = [_text(p) for p in paras[: heads[0]] if _text(p)]
    joined = " | ".join(header_text)
    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", joined)
    phone = re.search(r"(?:\+\d{1,3}[\s-]?)?\d{3,5}[\s-]?\d{3,5}[\s-]?\d{0,5}", joined)
    sections = []
    for n, h in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(paras)
        body = [i for i in range(h + 1, end) if _text(paras[i])]
        title = _text(paras[h])
        kind = classify_section(title)
        sec = {"index": n, "title": title, "kind": kind, "heading_para": h, "paras": body}
        sec.update(_layout(paras, body, kind))
        sections.append(sec)
    return {
        "format": "docx",
        "paragraph_count": len(paras),
        "header": {"name": header_text[0] if header_text else "", "email": email.group() if email else "",
                   "phone": phone.group().strip() if phone and sum(c.isdigit() for c in phone.group()) >= 8 else "",
                   "links": []},
        "sections": sections,
    }


def _layout(paras, body: list[int], kind: str) -> dict:
    if not body:
        return {"layout": "raw"}
    bullets = [i for i in body if _is_bullet(paras[i])]
    if kind == "skills":
        cats = []
        for i in body:
            m = re.match(r"^([^:]{2,40}):\s*(.+)$", _text(paras[i]))
            if m:
                cats.append({"name": m.group(1).strip(), "para": i, "value": m.group(2).strip(),
                             "items": [s.strip() for s in _split_top_level_commas(m.group(2)) if s.strip()]})
        if cats:
            return {"layout": "skills", "skills": cats}
    if bullets and len(bullets) < len(body):
        entries, cur = [], None
        for i in body:
            if _is_bullet(paras[i]):
                if cur is None:
                    cur = {"heading_paras": [], "bullet_paras": []}
                    entries.append(cur)
                cur["bullet_paras"].append(i)
            else:
                if cur is None or cur["bullet_paras"]:
                    cur = {"heading_paras": [], "bullet_paras": []}
                    entries.append(cur)
                cur["heading_paras"].append(i)
        out = []
        for e in entries:
            heading_text = " \t ".join(_text(paras[i]) for i in e["heading_paras"])
            out.append({
                "heading_paras": e["heading_paras"],
                "bullet_paras": e["bullet_paras"],
                "items": [{"text": re.sub(r"^[•▪◦●\-–]\s*", "", _text(paras[i]))} for i in e["bullet_paras"]],
                "fields": _fields(heading_text, kind),
            })
        return {"layout": "entries", "entries": out}
    if bullets:
        return {"layout": "items", "items": [{"text": re.sub(r"^[•▪◦●\-–]\s*", "", _text(paras[i])), "para": i}
                                             for i in bullets]}
    return {"layout": "paragraph", "paragraph": {"paras": body, "text": " ".join(_text(paras[i]) for i in body)}}


def _fields(text: str, kind: str) -> dict:
    parts = [p.strip() for p in re.split(r"\t+|\s{3,}|\s\|\s|\n", text) if p.strip()]
    fields: dict = {}
    rest = []
    for p in parts:
        if "dates" not in fields and DATE_RX.search(p) and len(p) <= 40:
            fields["dates"] = p
        else:
            rest.append(p)
    for role, val in zip(("primary", "secondary", "location"), rest, strict=False):
        fields[role] = val
    if kind == "education" and "secondary" in fields:
        m = GRADE_RX.search(fields["secondary"])
        if m:
            fields["grade"] = m.group(1).strip()
    if "primary" in fields:
        fields["short_name"] = re.split(r"\s+[—–-]{1,2}\s+|\s*\|\s*", fields["primary"], maxsplit=1)[0].strip()
    return fields


# --- rendering ------------------------------------------------------------------------------------

def _set_text(p, text: str) -> None:
    """Replaces a paragraph's text, keeping the formatting of its first text run."""
    runs = [r for r in p.iter(qn("w:r")) if r.find(qn("w:t")) is not None]
    if not runs:
        return
    first = runs[0]
    for t in first.findall(qn("w:t"))[1:]:
        first.remove(t)
    t = first.find(qn("w:t"))
    bullet_prefix = re.match(r"^([•▪◦●\-–]\s*)", _text(p))
    t.text = (bullet_prefix.group(1) if bullet_prefix and not _has_numbering(p) else "") + text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for r in runs[1:]:
        r.getparent().remove(r)


def _has_numbering(p) -> bool:
    ps = p.find(qn("w:pPr"))
    return ps is not None and ps.find(qn("w:numPr")) is not None


def _replace_in(p, old: str, new: str) -> bool:
    """Replaces `old` with `new` inside a paragraph even when `old` spans several runs."""
    if not old:
        return False
    ts = list(p.iter(qn("w:t")))
    full = "".join(t.text or "" for t in ts)
    idx = full.find(old)
    if idx < 0:
        return False
    pos = 0
    remaining_start, remaining_end = idx, idx + len(old)
    placed = False
    for t in ts:
        s = t.text or ""
        a, b = pos, pos + len(s)
        pos = b
        if b <= remaining_start or a >= remaining_end:
            continue
        lo, hi = max(remaining_start, a) - a, min(remaining_end, b) - a
        t.text = s[:lo] + ("" if placed else new) + s[hi:]
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        placed = True
    return placed


def render_docx(data: bytes, structure: dict, instructions: list[dict]) -> bytes:
    doc = _open(data)
    paras = _paragraphs(doc)
    if len(paras) != structure.get("paragraph_count"):
        raise DocxTemplateError("The template changed since it was parsed; upload it again.")
    sections = {s["index"]: s for s in structure["sections"]}
    for ins in instructions:
        sec = sections.get(ins["index"])
        if sec is None:
            continue
        if sec["layout"] == "paragraph" and "text" in ins:
            ids = sec["paragraph"]["paras"]
            _set_text(paras[ids[0]], ins["text"])
            for i in ids[1:]:
                paras[i].getparent().remove(paras[i])
        elif sec["layout"] == "skills" and "categories" in ins:
            wanted = {c["name"].lower(): c["items"] for c in ins["categories"]}
            for c in sec["skills"]:
                items = wanted.get(c["name"].lower())
                if items:
                    _replace_in(paras[c["para"]], c["value"], ", ".join(items))
        elif sec["layout"] == "items" and "items" in ins:
            _replace_list(paras, [it["para"] for it in sec["items"]], ins["items"])
        elif sec["layout"] == "entries" and "entries" in ins:
            _render_entries(paras, sec, ins["entries"])
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _replace_list(paras, para_ids: list[int], texts: list[str]) -> None:
    proto = paras[para_ids[0]]
    parent = proto.getparent()
    insert_at = list(parent).index(proto)
    new = []
    for t in texts:
        el = copy.deepcopy(proto)
        _set_text(el, t)
        new.append(el)
    for i in para_ids:
        paras[i].getparent().remove(paras[i])
    for off, el in enumerate(new):
        parent.insert(insert_at + off, el)


def _render_entries(paras, sec: dict, specs: list[dict]) -> None:
    entries = sec["entries"]
    all_ids = [i for e in entries for i in e["heading_paras"] + e["bullet_paras"]]
    first = paras[min(all_ids)]
    parent = first.getparent()
    insert_at = list(parent).index(first)
    built = []
    for spec in specs:
        src_idx = spec.get("source_index")
        base = entries[src_idx] if src_idx is not None else next((e for e in entries if e["bullet_paras"]), entries[0])
        heads = [copy.deepcopy(paras[i]) for i in base["heading_paras"]]
        if src_idx is None:
            f_old, f_new = base["fields"], spec.get("fields") or {}
            for role in ("primary", "dates", "secondary", "location"):
                if role in f_old:
                    for h in heads:
                        if _replace_in(h, f_old[role], f_new.get(role, "")):
                            break
        built += heads
        bullets = spec.get("bullets")
        if bullets is None:
            built += [copy.deepcopy(paras[i]) for i in base["bullet_paras"]]
        elif base["bullet_paras"]:
            proto = paras[base["bullet_paras"][0]]
            for text in bullets:
                el = copy.deepcopy(proto)
                _set_text(el, text.replace("**", ""))
                built.append(el)
    for i in all_ids:
        paras[i].getparent().remove(paras[i])
    for off, el in enumerate(built):
        parent.insert(insert_at + off, el)


def docx_to_pdf(data: bytes) -> tuple[bytes | None, str]:
    soffice = get_settings().soffice_path or shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None, ("PDF export for Word templates needs LibreOffice on the server. The tailored .docx is ready "
                      "to download.")
    with tempfile.TemporaryDirectory(prefix="cp-docx-") as tmp:
        path = os.path.join(tmp, "resume.docx")
        with open(path, "wb") as fh:
            fh.write(data)
        try:
            subprocess.run(  # noqa: S603 - fixed argv
                [soffice, "--headless", "--norestore", f"-env:UserInstallation=file://{tmp}/lo",
                 "--convert-to", "pdf", "--outdir", tmp, path],
                capture_output=True, timeout=get_settings().latex_timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "Converting the Word document to PDF took too long."
        pdf_path = os.path.join(tmp, "resume.pdf")
        if not os.path.exists(pdf_path):
            return None, "LibreOffice could not convert the Word document to PDF."
        with open(pdf_path, "rb") as fh:
            return fh.read(), ""
