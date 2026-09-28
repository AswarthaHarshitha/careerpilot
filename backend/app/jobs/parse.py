"""Deterministic analysis of a job description.

This works without any language model. It finds requirement sections, classifies each skill mention as
required / preferred / keyword by where it appears, and extracts experience, education, job id, work mode
and an application deadline - the deadline only when the text states one, including its year.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from dateutil import parser as dateparser

from app.matching.taxonomy import category_of, display_name, find_skills

SECTION_RULES: list[tuple[str, re.Pattern]] = [
    ("preferred", re.compile(r"prefer|nice[\s-]to[\s-]have|bonus|plus|desired|good[\s-]to[\s-]have|additional (skills|"
                             r"qualifications)|added advantage|pluses|would be great|extra credit", re.I)),
    ("required", re.compile(r"requirement|qualification|must[\s-]have|what you('ll)? (need|bring)|minimum|basic|"
                            r"required|who you are|skills|what we('re| are) looking for|you (have|bring|should have)|"
                            r"eligibility|competenc|experience|about you|your profile|technical", re.I)),
    ("responsibilities", re.compile(r"responsibilit|what you('ll| will) do|the role|role overview|duties|day[\s-]to[\s-]"
                                    r"day|you will|your impact|in this role|key tasks|job description|what you'll be "
                                    r"doing|about the (role|job|position)", re.I)),
    ("company", re.compile(r"about (us|the company|the team|[A-Z][\w&.\- ]{1,40})$|who we are|our (mission|story|team)|"
                           r"company overview|life at", re.I)),
    ("benefits", re.compile(r"benefit|perks|what we offer|compensation|salary|why join|equal opportunity|eeo|"
                            r"diversity", re.I)),
]
PREFERRED_CUES = re.compile(r"\b(preferred|nice[\s-]to[\s-]have|a plus|is a plus|bonus|good[\s-]to[\s-]have|desirable|"
                            r"advantageous|added advantage|optional|familiarity with|exposure to)\b", re.I)
REQUIRED_CUES = re.compile(r"\b(must|required|requirement|proficien\w*|strong|solid|hands[\s-]on|experience (in|with)|"
                           r"knowledge of|expertise|skilled|understanding of|ability to|working knowledge)\b", re.I)
DOMAIN_TERMS = {
    "fintech": "Fintech", "banking": "Banking", "payments": "Payments", "healthcare": "Healthcare",
    "e-commerce": "E-commerce", "ecommerce": "E-commerce", "insurance": "Insurance", "telecom": "Telecom",
    "supply chain": "Supply chain", "logistics": "Logistics", "saas": "SaaS", "edtech": "EdTech", "retail": "Retail",
    "automotive": "Automotive", "adtech": "AdTech", "gaming": "Gaming", "cybersecurity": "Cybersecurity",
    "erp": "ERP", "crm": "CRM", "capital markets": "Capital markets", "trading": "Trading", "iot": "IoT",
    "embedded": "Embedded systems", "semiconductor": "Semiconductors", "pharma": "Pharma",
}
DEGREE_RX = re.compile(
    r"\b(bachelor'?s?|master'?s?|b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|m\.?\s?e\b|b\.?\s?sc|m\.?\s?sc|bca|mca|mba|ph\.?\s?d|"
    r"b\.?s\.?\b|m\.?s\.?\b|degree|graduat(e|ion)|undergraduate|postgraduate|diploma|engineering)\b",
    re.I,
)
YEARS_RX = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*(?:years?|yrs?)(?:'|’)?\b[^.\n]{0,60}?"
    r"\bexperience|experience[^.\n]{0,40}?(\d{1,2})\s*(?:\+|plus)?\s*(?:years?|yrs?)",
    re.I,
)
FRESHER_RX = re.compile(r"\b(freshers?|entry[\s-]level|new grad(uate)?s?|recent graduates?|0\s*(-|–|to)\s*[12]\s*years?|"
                        r"no (prior )?experience (is )?required|campus hire|graduate (program|trainee))\b", re.I)
JOB_ID_RX = re.compile(
    r"\b(?:job|req(?:uisition)?|position|posting|reference|ref|vacancy|opening)\s*(?:id|no\.?|number|#|code)\s*[:#.\-]?\s*"
    r"([A-Z0-9][A-Z0-9_\-/]{2,30})\b",
    re.I,
)
DEADLINE_RX = re.compile(
    r"\b(apply by|application deadline|deadline(?: to apply| for applications)?|last date(?: to apply| of application|"
    r" for applications)?|applications? (?:close|closes|closing|will close)(?: on| date)?|closing date|apply before|"
    r"submit (?:your )?applications? (?:by|before)|registration (?:closes|deadline|ends)|applications? (?:due|accepted "
    r"until)|open until)\s*[:\-–]?\s*(?:on\s+)?([^\n.;]{4,40})",
    re.I,
)
EMPLOYMENT_RX = [
    ("Internship", re.compile(r"\binternship\b|\bintern\b", re.I)),
    ("Contract", re.compile(r"\bcontract(or)?\b|\bfreelance\b|\bc2h\b|contract[\s-]to[\s-]hire", re.I)),
    ("Part-time", re.compile(r"\bpart[\s-]time\b", re.I)),
    ("Full-time", re.compile(r"\bfull[\s-]time\b|\bpermanent\b", re.I)),
]
WORKPLACE_RX = [
    ("hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("remote", re.compile(r"\b(fully remote|remote[\s-]first|100% remote|work from home|wfh|remote)\b", re.I)),
    ("onsite", re.compile(r"\b(on[\s-]?site|in[\s-]office|work from office|wfo)\b", re.I)),
]
IMPORTANCE_RANK = {"required": 0, "preferred": 1, "keyword": 2}


@dataclass
class Requirement:
    kind: str
    label: str
    canonical: str
    category: str
    importance: str
    snippet: str


@dataclass
class JDAnalysis:
    requirements: list[Requirement] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    qualifications: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    min_years: float | None = None
    experience_text: str = ""
    education_text: str = ""
    job_code: str = ""
    employment_type: str = ""
    workplace_type: str = ""
    location: str = ""
    deadline: date | None = None
    deadline_source: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class Block:
    section: str  # required | preferred | responsibilities | company | benefits | general
    heading: str
    lines: list[str]


def split_sections(text: str) -> list[Block]:
    blocks: list[Block] = [Block("general", "", [])]
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _heading_text(line)
        if heading is not None:
            blocks.append(Block(_classify_heading(heading), heading, []))
            continue
        blocks[-1].lines.append(line)
    return [b for b in blocks if b.lines or b.heading]


def _heading_text(line: str) -> str | None:
    if line.startswith("## "):
        return line[3:].strip().rstrip(":")
    if line.startswith("- "):
        return None
    if len(line) <= 70 and line.endswith(":") and len(line.split()) <= 9:
        return line.rstrip(":").strip()
    if len(line) <= 50 and line.isupper() and len(line.split()) <= 6 and re.search(r"[A-Z]{3}", line):
        return line.title()
    return None


def _classify_heading(h: str) -> str:
    for name, rx in SECTION_RULES:
        if rx.search(h):
            return name
    return "general"


def _bullets(block: Block) -> list[str]:
    return [ln[2:].strip() for ln in block.lines if ln.startswith("- ") and len(ln) > 6]


def analyze_text(text: str) -> JDAnalysis:
    out = JDAnalysis()
    blocks = split_sections(text)
    has_req_sections = any(b.section in ("required", "preferred") for b in blocks)

    found: dict[str, Requirement] = {}
    for block in blocks:
        if block.section in ("company", "benefits"):
            continue
        for line in block.lines:
            body = line[2:] if line.startswith("- ") else line
            if block.section == "preferred":
                importance = "preferred"
            elif block.section == "required":
                importance = "preferred" if PREFERRED_CUES.search(body) else "required"
            elif not has_req_sections:
                if PREFERRED_CUES.search(body):
                    importance = "preferred"
                elif REQUIRED_CUES.search(body) or line.startswith("- "):
                    importance = "required"
                else:
                    importance = "keyword"
            elif block.section == "responsibilities":
                # technologies named in the day-to-day work are part of the job, not optional keywords
                importance = "preferred" if PREFERRED_CUES.search(body) else "required"
            else:
                importance = "preferred" if PREFERRED_CUES.search(body) else "keyword"
            for m in find_skills(body):
                req = Requirement("skill", display_name(m.key), m.key, category_of(m.key), importance, body[:300])
                prev = found.get(m.key)
                if prev is None or IMPORTANCE_RANK[importance] < IMPORTANCE_RANK[prev.importance]:
                    found[m.key] = req
    out.requirements = sorted(found.values(), key=lambda r: (IMPORTANCE_RANK[r.importance], r.category, r.label))

    for block in blocks:
        if block.section == "responsibilities":
            lines = _bullets(block) or block.lines[:6]
            out.responsibilities += [ln for ln in lines if not re.match(r"(job|req\w*)\s*(id|no)|location", ln, re.I)]
        elif block.section in ("required", "preferred"):
            out.qualifications += _bullets(block) or block.lines[:8]
    out.responsibilities = out.responsibilities[:15]
    out.qualifications = out.qualifications[:20]

    _experience(text, out)
    _education(blocks, out)
    _certifications(text, out)
    _domains(text, out)
    _deadline(text, out)
    m = JOB_ID_RX.search(text)
    if m and re.search(r"\d", m.group(1)):
        out.job_code = m.group(1).strip("-/.")
    for name, rx in EMPLOYMENT_RX:
        if rx.search(text[:4000]):
            out.employment_type = name
            break
    for name, rx in WORKPLACE_RX:
        if rx.search(text[:6000]):
            out.workplace_type = name
            break
    loc = re.search(r"^(?:-\s*)?(?:job\s+)?location[s]?\s*[:\-–]\s*(.{2,80})$", text, re.I | re.M)
    if loc:
        out.location = loc.group(1).strip()
    out.keywords = [r.label for r in out.requirements if r.kind == "skill"][:40]
    return out


def _experience(text: str, out: JDAnalysis) -> None:
    best: tuple[float, str, str] | None = None
    for m in YEARS_RX.finditer(text):
        n = m.group(1) or m.group(3)
        try:
            years = float(n)
        except (TypeError, ValueError):
            continue
        if years > 25:
            continue
        sentence = _sentence(text, m.start(), m.end())
        span = f"{years:g}\u2013{m.group(2)} years" if m.group(2) else f"{years:g}+ years"
        if best is None or years < best[0]:
            best = (years, sentence, span)
    fresher = FRESHER_RX.search(text)
    label = ""
    if best:
        out.min_years, out.experience_text, span = best
        label = f"{span} of experience"
    elif fresher:
        out.min_years = 0
        out.experience_text = _sentence(text, fresher.start(), fresher.end())
        label = "Entry level / new graduate"
    if out.experience_text:
        out.requirements.append(Requirement("experience", label, "", "experience", "required", out.experience_text))


def _education(blocks: list[Block], out: JDAnalysis) -> None:
    for b in blocks:
        if b.section in ("company", "benefits"):
            continue
        for line in b.lines:
            body = line[2:] if line.startswith("- ") else line
            if DEGREE_RX.search(body) and re.search(r"degree|bachelor|master|b\.?\s?tech|b\.?e\b|graduat|m\.?\s?tech|"
                                                    r"computer science|engineering|b\.?s\b|m\.?s\b|mca|bca|ph\.?d",
                                                    body, re.I):
                if len(body) > 400:
                    continue
                out.education_text = body
                importance = "preferred" if b.section == "preferred" or PREFERRED_CUES.search(body) else "required"
                out.requirements.append(Requirement("education", body[:160], "", "education", importance, body))
                return


def _certifications(text: str, out: JDAnalysis) -> None:
    for m in re.finditer(r"[^.\n]*\b(certification|certified|certificate)\b[^.\n]*", text, re.I):
        s = m.group().strip(" -")
        if 10 < len(s) < 250 and not re.search(r"equal opportunity|background", s, re.I):
            imp = "preferred" if PREFERRED_CUES.search(s) else "required"
            out.requirements.append(Requirement("certification", s[:160], "", "certification", imp, s))
            break


def _domains(text: str, out: JDAnalysis) -> None:
    low = text.lower()
    seen = set()
    for term, label in DOMAIN_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", low) and label not in seen:
            seen.add(label)
            m = re.search(rf"\b{re.escape(term)}\b", low)
            out.requirements.append(Requirement("domain", label, "", "domain", "keyword",
                                                _sentence(text, m.start(), m.end())))


def parse_explicit_date(fragment: str) -> date | None:
    """Parses a date only if it names a day, a month and a year. Returns None for anything vaguer."""
    frag = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", fragment)
    frag = re.split(r"\b(at|by|before|\d{1,2}:\d{2}|\d{1,2}\s*(am|pm)|IST|UTC|GMT|EST|PST|midnight)\b", frag, flags=re.I)[0]
    if not re.search(r"\b(19|20)\d{2}\b", frag):
        return None
    has_month_word = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", frag, re.I)
    numeric = re.search(r"\b\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}\b", frag)
    if not (has_month_word and re.search(r"\b\d{1,2}\b(?![/.\-]\d)", frag)) and not numeric:
        return None
    try:
        return dateparser.parse(frag, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None


def _deadline(text: str, out: JDAnalysis) -> None:
    for m in DEADLINE_RX.finditer(text):
        phrase = m.group(0).strip()
        d = parse_explicit_date(m.group(2))
        if d:
            out.deadline = d
            out.deadline_source = f'Stated in the posting: "{phrase[:120]}"'
            return
        if re.search(r"\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", m.group(2), re.I):
            out.notes.append(f'The posting mentions a deadline ("{phrase[:100]}") without a full date, so none was '
                             "recorded.")
            return


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start))
    right_c = [i for i in (text.find("\n", end), text.find(". ", end)) if i >= 0]
    right = min(right_c) if right_c else len(text)
    return text[left + 1 : right + 1].strip(" -\n")[:300]
