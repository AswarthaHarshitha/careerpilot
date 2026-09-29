"""Plain-text and Markdown resumes: read their structure and render them in the standard layout.

Text has no visual design to preserve, so the section order, headings, entries, bullets and skills are kept and
the standard CareerPilot layout provides the typography.
"""

import re

from app.resume.house_template import build
from app.resume.latex_template import DATE_RX, classify_section

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"https?://\S+|(?:www\.)?(?:github|linkedin)\.com/\S+", re.I)
PHONE = re.compile(r"\+?\d[\d\s()-]{7,}\d")


def _heading(line: str) -> str | None:
    m = re.match(r"^#{1,3}\s+(.+)$", line)
    if m:
        return m.group(1).strip()
    bare = line.strip().rstrip(":")
    if 2 < len(bare) < 40 and (bare.isupper() or line.strip().endswith(":")) and classify_section(bare) != "other":
        return bare.title() if bare.isupper() else bare
    return None


def parse_text(text: str) -> dict:
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        raise ValueError("The file is empty.")
    name = re.sub(r"^#+\s*", "", lines[0]).strip()
    profile: dict = {"full_name": name}
    i = 1
    while i < len(lines) and _heading(lines[i]) is None:
        ln = lines[i]
        if m := EMAIL.search(ln):
            profile["email"] = m.group()
        for u in URL.findall(ln):
            u = u if u.startswith("http") else "https://" + u
            key = "github_url" if "github.com" in u else "linkedin_url" if "linkedin.com" in u else "portfolio_url"
            profile[key] = u.rstrip(").,")
        if (m := PHONE.search(EMAIL.sub("", ln))) and sum(c.isdigit() for c in m.group()) >= 8:
            profile["phone"] = m.group().strip()
        i += 1
    sections: list[tuple[str, list[str]]] = []
    for ln in lines[i:]:
        h = _heading(ln)
        if h:
            sections.append((h, []))
        elif sections:
            sections[-1][1].append(ln)
    exps, projects, skills, edus, certs, achievements = [], [], {}, [], [], []
    for title, body in sections:
        kind = classify_section(title)
        if kind == "summary":
            profile["summary"] = " ".join(b.strip() for b in body)
        elif kind == "skills":
            for b in body:
                m = re.match(r"^[-*•]?\s*\**([^:*]{2,40})\**\s*:\s*(.+)$", b)
                if m:
                    skills[m.group(1).strip()] = [x.strip() for x in m.group(2).split(",") if x.strip()]
        elif kind in ("experience", "projects", "education"):
            current = None
            for b in body:
                bullet = re.match(r"^\s*[-*•]\s+(.+)$", b)
                if bullet and current is not None:
                    text = bullet.group(1).strip()
                    tm = re.match(r"^\**(technologies|tech stack)\**\s*:\s*(.+)$", text, re.I)
                    if tm:
                        current["tech"] = [x.strip(" .") for x in tm.group(2).split(",") if x.strip()]
                    else:
                        current["bullets"].append(text)
                    continue
                clean = re.sub(r"[*_#]", "", b).strip()
                parts = [p.strip() for p in re.split(r"\s+\|\s+|\s{2,}|\s+[—–]\s+(?=\S*\d)", clean) if p.strip()]
                dates = next((p for p in parts if DATE_RX.search(p) and len(p) < 40), "")
                rest = [p for p in parts if p != dates]
                if (
                    current is not None
                    and not current["bullets"]
                    and not current.get("title2")
                    and rest
                    and kind != "projects"
                ):
                    current["title2"] = rest[0]
                    current["location"] = rest[1] if len(rest) > 1 else ""
                    continue
                url = next((u for u in URL.findall(b)), "")
                current = {
                    "name": rest[0] if rest else clean,
                    "dates": dates,
                    "bullets": [],
                    "tech": [],
                    "url": url,
                    "extra": rest[1:],
                }
                {"experience": exps, "projects": projects, "education": edus}[kind].append(current)
        elif kind in ("certifications", "achievements"):
            items = [re.sub(r"^\s*[-*•]\s+", "", b).strip() for b in body]
            (certs if kind == "certifications" else achievements).extend(x for x in items if x)
    return {
        "profile": profile,
        "experiences": [
            {
                "organization": e["name"],
                "dates": e["dates"],
                "title": e.get("title2", ""),
                "location": e.get("location", ""),
                "bullets": e["bullets"],
                "tech": e["tech"],
            }
            for e in exps
        ],
        "projects": [
            {"title": p["name"], "url": p["url"], "bullets": p["bullets"], "tech": p["tech"]} for p in projects
        ],
        "skills": skills,
        "educations": [
            {"institution": e["name"], "degree": e.get("title2", ""), "end": e["dates"], "grade": ""} for e in edus
        ],
        "certifications": certs,
        "achievements": achievements,
    }


def text_to_latex(text: str) -> str:
    d = parse_text(text)
    return build(
        d["profile"],
        d["experiences"],
        d["projects"],
        d["skills"],
        d["educations"],
        d["certifications"],
        d["achievements"],
    )
