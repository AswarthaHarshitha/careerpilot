"""Pre-export gate: authenticity, language quality, instruction compliance and PDF validation.

Every generated resume passes through here before it can be approved, downloaded or uploaded to an employer.
Checks report PASS / REVIEW / FAIL with the exact items involved. Any FAIL blocks export.

This is not an attempt to fool AI or plagiarism detectors, and it produces no "human score": it looks for the
concrete things that make a resume untrustworthy or unreadable - unsupported claims, invented numbers, generic
filler, copied text, keyword stuffing, hidden text, layout breakage - and names them.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from app.matching.taxonomy import SKILLS, display_name, find_skills
from app.resume.truth import NUMBER_RX

# Inflated or generic phrasing that says nothing concrete. Detected, not swapped for synonyms.
GENERIC_PHRASES = [
    r"leverag(?:e|ed|ing) cutting[\s-]edge", r"cutting[\s-]edge", r"harness(?:ed|ing)? the power", r"spearhead(?:ed|ing)?",
    r"passionate about", r"results[\s-]driven", r"proven track record", r"highly motivated", r"dynamic individual",
    r"revolutioni[sz](?:e|ed|ing)", r"seamlessly integrat(?:e|ed|ing)", r"state[\s-]of[\s-]the[\s-]art",
    r"demonstrated exceptional", r"played a pivotal role", r"pivotal", r"in today'?s (?:rapidly )?(?:evolving|fast)",
    r"synergi[sz](?:e|ed|ing)", r"robust and scalable", r"go[\s-]getter", r"thought leader", r"best[\s-]in[\s-]class",
    r"world[\s-]class", r"game[\s-]chang(?:er|ing)", r"innovative solution", r"transformative", r"utili[sz](?:e|ed|ing)",
    r"worked extensively", r"various technologies", r"sophisticated", r"deep dive", r"hands[\s-]on experience with a "
    r"wide range", r"team player", r"detail[\s-]oriented", r"self[\s-]starter", r"exceptional", r"extensive experience",
]
GENERIC_RX = re.compile(r"\b(?:" + "|".join(GENERIC_PHRASES) + r")\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|I'm|I've|me|my|we|our|us)\b")
PRESENT_OPENERS = {"build", "develop", "design", "implement", "create", "manage", "lead", "maintain", "write", "test",
                   "deploy", "analyze", "work", "use", "integrate", "optimize"}
PLACEHOLDERS = re.compile(r"\bundefined\b|\bNaN\b|\b[Ll]orem ipsum\b|\bTODO\b|\bTBD\b|\bXXX+\b|\[[Ii]nsert[^\]]*\]|"
                          r"\{\{[^}]*\}\}|\\[a-zA-Z]+\{|\?\?")
PROFILE_ASSET_NAME = re.compile(r"\b(portfolio|personal (?:web)?site|github profile)\b", re.I)


@dataclass
class Check:
    id: str
    label: str
    status: str = "pass"  # pass | review | fail
    detail: str = ""
    items: list[str] = field(default_factory=list)

    def fail(self, detail: str, items: list[str] | None = None):
        self.status, self.detail, self.items = "fail", detail, (items or [])[:20]

    def review(self, detail: str, items: list[str] | None = None):
        if self.status != "fail":
            self.status, self.detail, self.items = "review", detail, (items or [])[:20]


def _bullets(content: dict) -> list[dict]:
    out = []
    for s in content.get("sections", []):
        if s.get("removed"):
            continue
        for e in s.get("entries") or []:
            for b in e.get("bullets") or []:
                out.append({"section": s["title"], "entry": e.get("fields", {}).get("primary", ""), **b})
    return out


def _texts(content: dict) -> list[str]:
    t = [b["text"] for b in _bullets(content)]
    for s in content.get("sections", []):
        if s.get("removed"):
            continue
        if s.get("layout") == "paragraph" and s.get("text"):
            t.append(s["text"])
        t += s.get("items") or []
    return t


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9+#]+", text.lower())
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def language_checks(content: dict, jd_text: str, sources: list[str]) -> list[Check]:
    bullets = _bullets(content)
    texts = _texts(content)
    checks = []

    generic = Check("generic_language", "Generic or inflated wording")
    hits = [f"“{m.group()}” in: {t[:80]}" for t in texts for m in [GENERIC_RX.search(t)] if m]
    if hits:
        generic.review(f"{len(hits)} phrase(s) say nothing concrete; rewrite from what was actually built.", hits)
    checks.append(generic)

    person = Check("first_person", "First-person language")
    fp = [t[:80] for t in (b["text"] for b in bullets) if FIRST_PERSON.search(t)]
    if fp:
        person.review("Resume bullets should not use I / my / we.", fp)
    checks.append(person)

    openers = Counter(b["text"].split()[0].lower().strip(",.") for b in bullets if b["text"].split())
    repeated = Check("repetition", "Repeated openings and phrases")
    rep = [f"“{w.capitalize()}” starts {n} bullets" for w, n in openers.items() if n >= 3]
    tri = Counter(g for t in (b["text"] for b in bullets) for g in _ngrams(t, 4))
    rep += [f"“{' '.join(g)}” used {n} times" for g, n in tri.items() if n >= 3]
    if rep:
        repeated.review("Vary the openings; repetition reads as templated text.", rep)
    checks.append(repeated)

    tense = Check("tense", "Consistent tense")
    present = [b["text"][:80] for b in bullets if b["text"].split() and b["text"].split()[0].lower() in PRESENT_OPENERS]
    if present:
        tense.review("Bullets should use past tense for completed work.", present)
    checks.append(tense)

    names = Check("tech_names", "Consistent technology names")
    seen: dict[str, set[str]] = {}
    for t in texts:
        for m in find_skills(t, include_weak=False):
            seen.setdefault(m.key, set()).add(t[m.start : m.end])
    incons = [f"{display_name(k)}: {', '.join(sorted(v))}" for k, v in seen.items()
              if len({re.sub(r'[^a-z0-9]', '', x.lower()).rstrip('s') for x in v}) > 1 and k in SKILLS]
    if incons:
        names.review("The same technology is spelled differently.", incons)
    checks.append(names)

    length = Check("bullet_length", "Concise bullets")
    long = [b["text"][:80] for b in bullets if len(b["text"].split()) > 35]
    if long:
        length.review(f"{len(long)} bullet(s) exceed 35 words.", long)
    checks.append(length)

    copied = Check("copied_text", "Original wording (not copied from the JD or READMEs)")
    jd_grams = _ngrams(jd_text, 8)
    src_grams = set().union(*[_ngrams(s, 12) for s in sources]) if sources else set()
    cp = []
    for b in bullets:
        if b.get("origin") in ("template", "profile", "user"):
            continue  # the user's own wording
        g8 = _ngrams(b["text"], 8)
        if g8 & jd_grams:
            cp.append(f"Copied from the job description: {b['text'][:80]}")
        elif _ngrams(b["text"], 12) & src_grams:
            cp.append(f"Copied from a README/description: {b['text'][:80]}")
    if cp:
        copied.review("Rewrite these in your own words; keep only the necessary technical terms.", cp)
    checks.append(copied)
    return checks


def evidence_checks(content: dict, eligible_projects: dict[int, dict], allowed_skill_text: set[str],
                    strong_keys: set[str]) -> list[Check]:
    """eligible_projects: project id -> {"title", "eligible": bool, "asset_type"}"""
    checks = []
    portfolio = Check("portfolio_project", "Portfolio is not listed as a project")
    projects = Check("project_eligibility", "Every listed project is a finished, eligible project")
    for s in content.get("sections", []):
        if s.get("kind") != "projects" or s.get("removed"):
            continue
        for e in s.get("entries") or []:
            name = e.get("fields", {}).get("primary", "")
            ref = e.get("ref") or {}
            info = eligible_projects.get(ref.get("id")) if ref.get("type") == "project" else None
            if (info and info["asset_type"] == "personal_profile") or PROFILE_ASSET_NAME.search(name):
                portfolio.fail("A portfolio/profile asset appears in the Projects section.", [name])
            elif info is not None and not info["eligible"]:
                projects.fail("Listed project is excluded or not finished.", [name])
    checks += [portfolio, projects]

    skills = Check("skill_evidence", "Every listed skill is backed by your profile or evidence")
    bad = []
    for s in content.get("sections", []):
        if s.get("layout") != "skills" or s.get("removed"):
            continue
        for c in s.get("categories", []):
            original = {x.lower() for x in c.get("original") or []}
            for item in c.get("items", []):
                if item.lower() in original or item.lower() in allowed_skill_text:
                    continue
                keys = {m.key for m in find_skills(item)}
                if not keys or not keys <= strong_keys:
                    bad.append(item)
    if bad:
        skills.fail("These skills were added without evidence.", bad)
    checks.append(skills)

    metrics = Check("metrics", "Every number is verified")
    bad_nums = []
    for b in _bullets(content):
        if b.get("origin") in ("user", "template", "profile"):
            continue
        new = {re.sub(r"[\s,]", "", m.group()).lower() for m in NUMBER_RX.finditer(b["text"])}
        old = {re.sub(r"[\s,]", "", m.group()).lower() for m in NUMBER_RX.finditer(b.get("original", ""))}
        extra = new - old
        if extra:
            bad_nums.append(f"{', '.join(sorted(extra))} in: {b['text'][:80]}")
    if bad_nums:
        metrics.fail("Numbers not present in your source material.", bad_nums)
    checks.append(metrics)
    return checks


def instruction_checks(cfg: dict, content: dict, rendered_source: str | None, page_count: int | None,
                       header_urls: dict[str, str]) -> list[Check]:
    c = Check("instructions", "Job-specific instructions applied")
    problems = []
    if rendered_source is not None:
        for kind, mode in cfg.get("links", {}).items():
            url = header_urls.get(kind, "")
            if mode == "exclude" and url and url in rendered_source.split("\\section", 1)[0]:
                problems.append(f"{kind} link still present")
            if mode == "include" and url and url not in rendered_source:
                problems.append(f"{kind} link missing")
            if mode == "include" and not url:
                problems.append(f"{kind} link requested but no {kind} URL is in your profile")
    for s in content.get("sections", []):
        if s["kind"] in ("summary", "certifications") and cfg.get(s["kind"]) == "exclude" and not s.get("removed"):
            problems.append(f"{s['title']} should have been removed")
    proj = next((s for s in content.get("sections", []) if s["kind"] == "projects" and not s.get("removed")), None)
    if proj and cfg.get("projects_explicit") and len(proj.get("entries") or []) > cfg["projects"]:
        problems.append(f"{len(proj['entries'])} projects listed; {cfg['projects']} requested")
    if proj and cfg.get("project_priority"):
        names = [e.get("fields", {}).get("short_name") or e.get("fields", {}).get("primary", "")
                 for e in proj.get("entries") or []]
        want = [n.lower() for n in cfg["project_priority"]]
        got = [n.lower() for n in names if any(w in n.lower() for w in want)]
        if got and [w for w in want if any(w in g for g in got)] != [next(w for w in want if w in g) for g in got]:
            problems.append("project order differs from your instruction")
    if problems:
        c.fail("Some instructions were not satisfied.", problems)
    if cfg.get("conflicts"):
        c.review(f"{len(cfg['conflicts'])} instruction(s) were not applied because they conflict with verified "
                 "evidence.", [x["instruction"] for x in cfg["conflicts"]])
    length = Check("length", "Resume length")
    if page_count and page_count > cfg.get("length", 99):
        length.fail(f"The resume is {page_count} pages; the target is {cfg['length']}.")
    elif page_count:
        length.detail = f"{page_count} page{'s' if page_count > 1 else ''}"
    return [c, length]


def pdf_checks(pdf: bytes, header: dict, section_titles: list[str], name_hint: str = "") -> list[Check]:
    """Renders every page, inspects the text layout and verifies ATS text extraction and reading order."""
    ats = Check("ats_extraction", "ATS text extraction and reading order")
    layout = Check("layout", "Layout (no clipping, overlap or orphaned headings)")
    hidden = Check("hidden_text", "No hidden or unreadably small text")
    placeholders = Check("placeholders", "No placeholders or broken characters")
    try:
        doc = pymupdf.open(stream=pdf, filetype="pdf")
    except Exception:  # noqa: BLE001
        ats.fail("The PDF could not be opened.")
        return [ats]
    text = "\n".join(p.get_text() for p in doc)
    norm = re.sub(r"\s+", " ", text)
    missing = []
    name = header.get("name") or name_hint
    if name and name.split()[0].lower() not in norm.lower():
        missing.append("name")
    if header.get("email") and header["email"].lower() not in norm.lower():
        missing.append("email")
    if len(norm) < 300:
        missing.append("body text")
    order = []
    low = norm.lower()
    for t in section_titles:
        i = low.find(t.lower())
        if i < 0:
            missing.append(f"section “{t}”")
        order.append(i)
    found = [i for i in order if i >= 0]
    if missing:
        ats.fail("Not readable in the PDF text layer: " + ", ".join(missing))
    elif found != sorted(found):
        ats.review("Sections are extracted in a different order than they appear.")
    clipped, overlaps, orphans, tiny, white, blanks = [], [], [], [], [], 0
    for pno, page in enumerate(doc):
        rect = page.rect
        lines = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                bbox = pymupdf.Rect(line["bbox"])
                txt = "".join(s["text"] for s in spans).strip()
                lines.append((bbox, txt, spans))
                if bbox.x1 > rect.x1 + 1 or bbox.y1 > rect.y1 + 1 or bbox.x0 < -1 or bbox.y0 < -1:
                    clipped.append(f"p{pno + 1}: {txt[:50]}")
                for s in spans:
                    if s["size"] < 5.5:
                        tiny.append(f"p{pno + 1}: {s['text'][:40]} ({s['size']:.1f}pt)")
                    if s["color"] in (0xFFFFFF, 0xFEFEFE):
                        white.append(f"p{pno + 1}: {s['text'][:40]}")
        for i, (a, ta, _) in enumerate(lines):
            for b, tb, _ in lines[i + 1 :]:
                inter = a & b
                if not inter.is_empty and inter.get_area() > 0.35 * min(a.get_area(), b.get_area()) and ta != tb:
                    overlaps.append(f"p{pno + 1}: “{ta[:30]}” / “{tb[:30]}”")
        if lines:
            last = max(lines, key=lambda x: x[0].y1)
            if any(last[1].strip().lower() == t.lower() for t in section_titles) and pno < len(doc) - 1:
                orphans.append(f"“{last[1]}” at the bottom of page {pno + 1}")
        else:
            blanks += 1
    if clipped:
        layout.fail("Text runs outside the page.", clipped)
    if overlaps:
        layout.fail("Overlapping text.", overlaps)
    if orphans:
        layout.review("A heading is separated from its content.", orphans)
    if blanks:
        layout.fail(f"{blanks} blank page(s).")
    if tiny or white:
        hidden.fail("Text that a reader cannot see is not allowed.", tiny + white)
    ph = [m.group() for m in PLACEHOLDERS.finditer(text)]
    lines_all = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 25]
    dups = [ln for ln, n in Counter(lines_all).items() if n > 1]
    if ph:
        placeholders.fail("Placeholder or markup text in the PDF.", ph)
    if dups:
        placeholders.review("Duplicated lines.", dups)
    if "\ufffd" in text:
        placeholders.fail("Broken characters (�) in the PDF.")
    return [ats, layout, hidden, placeholders]


def stuffing_check(pdf_text: str) -> Check:
    c = Check("keyword_stuffing", "No keyword stuffing")
    counts = Counter(m.key for m in find_skills(pdf_text))
    heavy = [f"{display_name(k)} ×{n}" for k, n in counts.items() if n >= 9]
    if heavy:
        c.review("Some keywords repeat heavily.", heavy)
    return c


def summarize(checks: list[Check], extra: dict | None = None) -> dict:
    return {
        "checks": [c.__dict__ for c in checks],
        "blocked": any(c.status == "fail" for c in checks),
        "counts": {s: sum(1 for c in checks if c.status == s) for s in ("pass", "review", "fail")},
        **(extra or {}),
    }


def page_images(pdf: bytes, zoom: float = 1.4) -> list[bytes]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return [p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png") for p in doc]
