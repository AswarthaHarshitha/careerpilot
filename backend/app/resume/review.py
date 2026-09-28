"""ATS and recruiter checks for a generated resume.

Both checks report findings and plain labels (Strong / Good / Fair / Weak, Low / Medium / High risk) with the
reasons behind them. Neither produces a numeric "score": the goal is to show what to fix.

The ATS check reads the text extracted from the compiled PDF - what an applicant-tracking system sees -
not the source.
"""

import re
import unicodedata
from collections import Counter

from app.matching.taxonomy import display_name, find_skills

STANDARD_HEADINGS = re.compile(r"summary|profile|objective|experience|employment|work|project|skill|technical|"
                               r"education|certification|achievement|award|publication|leadership|activit", re.I)
STRONG_VERBS = {
    "built", "developed", "designed", "implemented", "engineered", "created", "led", "architected", "automated",
    "optimized", "optimised", "deployed", "integrated", "migrated", "reduced", "improved", "delivered", "launched",
    "analyzed", "analysed", "tested", "debugged", "refactored", "maintained", "configured", "modeled", "modelled",
    "trained", "evaluated", "validated", "researched", "published", "applied", "performed", "wrote", "established",
    "streamlined", "containerized", "containerised", "orchestrated", "secured", "authored", "coordinated", "managed",
    "prototyped", "shipped", "scaled", "instrumented", "documented", "extended", "added", "introduced", "enabled",
}
STUFFING_THRESHOLD = 9
WEAK_OPENERS = re.compile(r"^(worked on|responsible for|helped|assisted|involved in|participated in|tasked with|"
                          r"duties included|was part of|did)\b", re.I)


def level(ratio: float) -> str:
    if ratio >= 0.85:
        return "Strong"
    if ratio >= 0.65:
        return "Good"
    if ratio >= 0.4:
        return "Fair"
    return "Weak"


def _resume_bullets(content: dict) -> list[tuple[str, str]]:
    out = []
    for s in content.get("sections", []):
        for e in s.get("entries") or []:
            for b in e.get("bullets") or []:
                out.append((s["title"], b["text"]))
    return out


def ats_check(pdf_text: str, content: dict, requirements: list[dict], matches: dict[int, str], page_count: int | None,
              max_pages: int, header: dict) -> dict:
    """requirements: [{id, kind, label, canonical, importance}]; matches: requirement id -> match status."""
    text = unicodedata.normalize("NFKC", pdf_text or "")
    joined = _join_split_words(text)
    present = {m.key: joined[m.start:m.end] for m in find_skills(joined)}
    findings: list[dict] = []

    coverage = {}
    missing_with_evidence, missing_no_evidence = [], []
    for imp in ("required", "preferred"):
        reqs = [r for r in requirements if r["kind"] == "skill" and r["importance"] == imp and r["canonical"]]
        covered = [r for r in reqs if r["canonical"] in present]
        coverage[imp] = {"covered": len(covered), "total": len(reqs),
                         "level": level(len(covered) / len(reqs)) if reqs else "n/a"}
        for r in reqs:
            if r["canonical"] in present:
                continue
            status = matches.get(r["id"], "missing")
            (missing_with_evidence if status == "strong" else missing_no_evidence).append(
                {"label": r["label"], "importance": imp, "match": status})
    if missing_with_evidence:
        findings.append({"severity": "high", "title": "Relevant skills you have are not on the resume",
                         "detail": ", ".join(m["label"] for m in missing_with_evidence) + " - your projects "
                         "demonstrate these; add them to the skills section or a bullet."})
    if missing_no_evidence:
        findings.append({"severity": "info", "title": "Requirements you have no evidence for",
                         "detail": ", ".join(m["label"] for m in missing_no_evidence[:10]) + ". These are gaps, not "
                         "wording problems - they were not added."})

    terminology = []
    jd_terms = {r["canonical"]: r["label"] for r in requirements if r["kind"] == "skill" and r["canonical"]}
    for key, jd_label in jd_terms.items():
        alias = present.get(key)
        # only a different name for the skill counts (e.g. "Postgres" vs "PostgreSQL"), not case or plural
        if alias and _loose(alias).rstrip("s") != _loose(jd_label).rstrip("s") and _loose(jd_label) not in _loose(joined):
            terminology.append({"resume": alias, "posting": jd_label})
    if terminology:
        findings.append({"severity": "low", "title": "Terminology differs from the posting",
                         "detail": "; ".join(f"resume says “{t['resume']}”, posting says “{t['posting']}”"
                                             for t in terminology[:6])})

    headings = [s["title"] for s in content.get("sections", [])]
    nonstandard = [h for h in headings if not STANDARD_HEADINGS.search(h)]
    if nonstandard:
        findings.append({"severity": "low", "title": "Non-standard section headings",
                         "detail": "ATS parsers may not recognise: " + ", ".join(nonstandard)})

    risks = []
    pua = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
    first_lines = "\n".join(text.splitlines()[:4])
    stray = re.findall(r"(?:(?<=\s)|^)[^\w\s@+().,|:/-](?=\s)|(?<=\|)\s*[a-z\]]\s+(?=[A-Z])", first_lines)
    if pua or stray:
        risks.append("Icons in the contact line extract as stray characters; some ATS parsers may misread the "
                     "contact details. Text labels next to icons keep them readable.")
    email = header.get("email", "")
    if email and email.lower() not in joined.lower():
        risks.append("The email address is not readable in the PDF text layer.")
    phone_digits = re.sub(r"\D", "", header.get("phone", ""))
    if phone_digits and phone_digits[-8:] not in re.sub(r"\D", "", joined):
        risks.append("The phone number is not readable in the PDF text layer.")
    name = header.get("name", "")
    if name and name.split()[0].lower() not in joined.lower()[:300]:
        risks.append("Your name does not appear at the top of the extracted text.")
    if len(joined.strip()) < 400:
        risks.append("Very little text could be extracted from the PDF.")
    for r in risks:
        findings.append({"severity": "medium", "title": "Parsing risk", "detail": r})

    counts = Counter(m.key for m in find_skills(joined))
    stuffing = [display_name(k) for k, n in counts.items() if n >= STUFFING_THRESHOLD]
    if stuffing:
        findings.append({"severity": "medium", "title": "Possible keyword repetition",
                         "detail": ", ".join(stuffing) + f" appear {STUFFING_THRESHOLD}+ times; repetition reads as "
                                                         "stuffing."})

    bullets = _resume_bullets(content)
    long_bullets = [b for _, b in bullets if len(b.split()) > 35]
    if long_bullets:
        findings.append({"severity": "low", "title": "Long bullets",
                         "detail": f"{len(long_bullets)} bullet(s) exceed 35 words."})

    pages_ok = page_count is not None and page_count <= max_pages
    if page_count and not pages_ok:
        findings.append({"severity": "medium", "title": "Over the page target",
                         "detail": f"The resume is {page_count} pages; your target is {max_pages}."})

    formatting_risk = "Low"
    if risks or not pages_ok:
        formatting_risk = "Medium"
    if len(risks) >= 2 or (page_count and page_count > max_pages + 1):
        formatting_risk = "High"
    req_cov = coverage.get("required", {})
    kw_ratio = (req_cov.get("covered", 0) / req_cov["total"]) if req_cov.get("total") else 1.0
    return {
        "summary": {
            "jd_coverage": req_cov.get("level", "n/a"),
            "keyword_alignment": level(kw_ratio) if not terminology else ("Good" if kw_ratio >= 0.65 else level(kw_ratio)),
            "formatting_risk": formatting_risk,
            "pages": page_count,
        },
        "coverage": coverage,
        "missing_with_evidence": missing_with_evidence,
        "missing_without_evidence": missing_no_evidence,
        "terminology": terminology,
        "parsing_risks": risks,
        "findings": findings,
    }


def recruiter_check(content: dict, requirements: list[dict], matches: dict[int, str], page_count: int | None,
                    max_pages: int) -> dict:
    findings: list[dict] = []
    bullets = _resume_bullets(content)
    req_keys = {r["canonical"]: r["importance"] for r in requirements if r["kind"] == "skill" and r["canonical"]}

    projects = next((s for s in content.get("sections", []) if s["kind"] == "projects"), None)
    proj_entries = projects.get("entries", []) if projects else []
    relevant = [e for e in proj_entries if e.get("matched")]
    if proj_entries:
        ratio = len(relevant) / len(proj_entries)
        if ratio < 1:
            findings.append({"severity": "medium", "title": "Project selection",
                             "detail": f"{len(proj_entries) - len(relevant)} project(s) show no evidence for this "
                                       "job's requirements."})
    else:
        ratio = 0.0

    weak = [b for _, b in bullets if WEAK_OPENERS.search(b)]
    if weak:
        findings.append({"severity": "medium", "title": "Weak bullet openings",
                         "detail": f"{len(weak)} bullet(s) start with phrases like “worked on” or "
                                   "“responsible for”; lead with what you built or did."})
    openers = Counter((b.split()[0].lower().strip(",.") if b.split() else "") for _, b in bullets)
    repeated = [w for w, n in openers.items() if n >= 3 and w]
    if repeated:
        findings.append({"severity": "low", "title": "Repeated opening verbs",
                         "detail": ", ".join(f"“{w.capitalize()}” ×{openers[w]}" for w in repeated)})
    no_verb = [b for _, b in bullets if b.split() and b.split()[0].lower().strip(",.") not in STRONG_VERBS
               and not WEAK_OPENERS.search(b)]
    technical = [b for _, b in bullets if find_skills(b, include_weak=False)]
    depth_ratio = len(technical) / len(bullets) if bullets else 0
    if bullets and depth_ratio < 0.5:
        findings.append({"severity": "low", "title": "Technical depth",
                         "detail": "Fewer than half the bullets name the technologies used."})
    long = [b for _, b in bullets if len(b.split()) > 32]
    short = [b for _, b in bullets if len(b.split()) < 8]
    if long:
        findings.append({"severity": "low", "title": "Concision", "detail": f"{len(long)} bullet(s) over 32 words."})
    if short:
        findings.append({"severity": "low", "title": "Thin bullets", "detail": f"{len(short)} bullet(s) under 8 words "
                                                                              "may not show enough substance."})
    outcome = [b for _, b in bullets if re.search(r"\b(reduc|improv|increas|enabl|prevent|detect|automat|cut|faster|"
                                                    r"reliab|secur)\w*", b, re.I)]
    if bullets and len(outcome) < max(1, len(bullets) // 4):
        findings.append({"severity": "info", "title": "Impact",
                         "detail": "Few bullets state an outcome. Where you have a real, measured result, add it; "
                                   "CareerPilot will not invent one."})

    skills_sec = next((s for s in content.get("sections", []) if s["layout"] == "skills"), None)
    listed = set()
    if skills_sec:
        for c in skills_sec.get("categories", []):
            for it in c["items"]:
                listed |= {m.key for m in find_skills(it)}
    backed = [k for k in listed if k in req_keys and matches_status(requirements, matches, k) == "strong"]
    claimed_relevant = [k for k in listed if k in req_keys]
    evidence_ratio = len(backed) / len(claimed_relevant) if claimed_relevant else 1.0
    unbacked = [display_name(k) for k in claimed_relevant if k not in backed]
    if unbacked:
        findings.append({"severity": "medium", "title": "Skills without demonstrated evidence",
                         "detail": ", ".join(unbacked[:8]) + " are listed but no project or experience on file "
                                   "demonstrates them; expect interview questions."})

    rejected = sum(len(b.get("rejected", [])) > 0 for s in content.get("sections", []) for e in s.get("entries") or []
                   for b in e.get("bullets") or [])
    gaps = [r for r in requirements if r["importance"] == "required" and matches.get(r["id"]) == "missing"]
    pages_label = f"{page_count} page{'s' if page_count != 1 else ''}" if page_count else "unknown"
    if page_count and page_count > max_pages:
        pages_label += f" (target {max_pages})"
    return {
        "summary": {
            "evidence_quality": level(evidence_ratio),
            "relevance": level(ratio) if proj_entries else "n/a",
            "bullet_quality": level(1 - (len(weak) + 0.5 * len(no_verb)) / len(bullets)) if bullets else "n/a",
            "technical_depth": level(depth_ratio) if bullets else "n/a",
            "potential_gaps": len(gaps),
            "resume_length": pages_label,
        },
        "gaps": [r["label"] for r in gaps],
        "rejected_rewrites": rejected,
        "findings": findings,
    }


def matches_status(requirements: list[dict], matches: dict[int, str], key: str) -> str:
    for r in requirements:
        if r.get("canonical") == key:
            return matches.get(r["id"], "missing")
    return "missing"


def _join_split_words(text: str) -> str:
    """PDF text extraction sometimes splits kerned capitals ("F oundation"); rejoin single capital letters."""
    return re.sub(r"\b([A-Z]) (?=[a-z]{2,})", r"\1", text)


def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", s.lower())
