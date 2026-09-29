"""HTML to structured plain text for job descriptions.

Headings become "## Heading" lines and list items become "- item" lines so later parsing can find
requirement sections. Scripts, styles and navigation are dropped; nothing from the page is ever rendered as
HTML in CareerPilot.
"""

import html as html_lib
import json
import re

from bs4 import BeautifulSoup, NavigableString, Tag

BLOCK_TAGS = {"p", "div", "section", "article", "ul", "ol", "table", "tr", "br", "header", "footer", "main"}
DROP_TAGS = [
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "iframe",
    "form",
    "button",
    "img",
    "picture",
    "video",
    "template",
    "canvas",
]


def html_to_text(fragment: str) -> str:
    if not fragment:
        return ""
    if "<" not in fragment and "&lt;" in fragment:
        fragment = html_lib.unescape(fragment)  # Greenhouse returns entity-escaped HTML
    soup = BeautifulSoup(fragment, "lxml")
    for t in soup(DROP_TAGS):
        t.decompose()
    lines: list[str] = []
    _walk(soup, lines)
    text = "\n".join(lines)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _walk(node, lines: list[str], buf: list[str] | None = None) -> None:
    buf = [] if buf is None else buf

    def flush():
        s = "".join(buf).strip()
        if s:
            lines.append(s)
        buf.clear()

    for child in node.children:
        if isinstance(child, NavigableString):
            if child.__class__.__name__ in ("Comment", "Doctype"):
                continue
            buf.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            flush()
            t = child.get_text(" ", strip=True)
            if t:
                lines.append("## " + t)
        elif name == "li":
            flush()
            t = child.get_text(" ", strip=True)
            if t:
                lines.append("- " + t)
        elif name in ("strong", "b") and _is_heading_like(child):
            flush()
            lines.append("## " + child.get_text(" ", strip=True).rstrip(":"))
        elif name == "br":
            flush()
        elif name in BLOCK_TAGS or name in ("dd", "dt", "td", "th", "blockquote"):
            flush()
            _walk(child, lines, [])
            flush()
        else:
            _walk(child, lines, buf)
    flush()


def _is_heading_like(tag: Tag) -> bool:
    text = tag.get_text(" ", strip=True)
    parent = tag.parent
    if not text or len(text) > 70 or parent is None:
        return False
    siblings_text = parent.get_text(" ", strip=True)
    return siblings_text == text


def extract_json_ld_jobposting(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for obj in _iter_ld(data):
            t = obj.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" in types:
                return obj
    return None


def _iter_ld(data):
    if isinstance(data, list):
        for d in data:
            yield from _iter_ld(d)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _iter_ld(data["@graph"])
        yield data


def main_content_text(soup: BeautifulSoup) -> str:
    """Picks the element most likely to hold the job description."""
    for t in soup(DROP_TAGS + ["header", "footer", "aside"]):
        t.decompose()
    candidates: list[Tag] = []
    for el in soup.find_all(["main", "article", "section", "div"]):
        ident = " ".join([el.get("id") or "", *(el.get("class") or [])]).lower()
        if re.search(r"job|description|posting|content|details|vacancy|position|opening", ident):
            candidates.append(el)
    candidates += soup.find_all(["main", "article"])
    best, best_score = None, 0
    for el in candidates:
        text = el.get_text(" ", strip=True)
        n_li = len(el.find_all("li"))
        score = len(text) + 200 * min(n_li, 30)
        # prefer the most specific container: penalise elements that are mostly nested candidates
        if score > best_score and len(text) < 60_000:
            best, best_score = el, score
    target = best or soup.body or soup
    return html_to_text(str(target))


def page_meta(soup: BeautifulSoup) -> dict:
    def meta(prop):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return (tag.get("content") or "").strip() if tag else ""

    h1 = soup.find("h1")
    return {
        "title": (soup.title.get_text(strip=True) if soup.title else ""),
        "og_title": meta("og:title"),
        "site_name": meta("og:site_name") or meta("application-name"),
        "h1": h1.get_text(" ", strip=True) if h1 else "",
    }
