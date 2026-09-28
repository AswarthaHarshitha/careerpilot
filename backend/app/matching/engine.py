"""Transparent job-to-profile matching.

Each requirement gets one of four statuses with the evidence behind it, never a percentage:

strong              demonstrated by a project or experience whose evidence is VERIFIED or USER-PROVIDED
partial             only listed in the profile's skills, or a closely related skill is demonstrated
needs_verification  only INFERRED evidence exists, or the profile lacks the data to decide (years, degree)
missing             no evidence at all
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.matching.taxonomy import display_name, find_skills, implied_keys, related_keys

CLAIMABLE = ("verified", "user_provided")
IMPORTANCE_WEIGHT = {"required": 3.0, "preferred": 1.5, "keyword": 1.0}


@dataclass
class EvidenceRef:
    type: str  # project | experience | skill | certification | education
    id: int
    label: str
    status: str  # verified | user_provided | inferred
    detail: str = ""
    via: str = ""  # set when the evidence is for a more specific skill (FastAPI -> Python)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in ("", None)}


@dataclass
class ProjectSnapshot:
    id: int
    title: str
    description: str
    evidence: list[tuple[str, str, str, str]]  # (canonical, status, label, detail)
    bullets: list[str]
    readme: str = ""
    pushed_at: datetime | None = None


@dataclass
class ExperienceSnapshot:
    id: int
    label: str
    text: str


@dataclass
class ProfileSnapshot:
    skills: dict[str, int] = field(default_factory=dict)  # canonical -> skill id
    experiences: list[ExperienceSnapshot] = field(default_factory=list)
    projects: list[ProjectSnapshot] = field(default_factory=list)
    certifications: list[tuple[int, str]] = field(default_factory=list)
    educations: list[tuple[int, str]] = field(default_factory=list)  # (id, "degree field institution")
    years_of_experience: float | None = None


@dataclass
class RequirementInput:
    id: int
    kind: str
    label: str
    canonical: str
    importance: str
    snippet: str = ""


@dataclass
class MatchResult:
    requirement_id: int
    status: str
    rationale: str
    evidence: list[EvidenceRef]


@dataclass
class ProjectScore:
    project_id: int
    score: float
    matched: list[dict]


def build_index(p: ProfileSnapshot) -> dict[str, list[EvidenceRef]]:
    idx: dict[str, list[EvidenceRef]] = {}

    def add(key: str, ref: EvidenceRef):
        idx.setdefault(key, []).append(ref)
        for parent in implied_keys(key):
            idx.setdefault(parent, []).append(EvidenceRef(ref.type, ref.id, ref.label, ref.status, ref.detail,
                                                          via=display_name(key)))

    for proj in p.projects:
        for canonical, status, label, detail in proj.evidence:
            if canonical:
                add(canonical, EvidenceRef("project", proj.id, proj.title, status, detail or label))
        for b in proj.bullets:
            for m in find_skills(b, include_weak=False):
                add(m.key, EvidenceRef("project", proj.id, proj.title, "user_provided", "your resume bullet"))
    for exp in p.experiences:
        for m in find_skills(exp.text, include_weak=False):
            add(m.key, EvidenceRef("experience", exp.id, exp.label, "user_provided", "your experience bullets"))
    for cid, name in p.certifications:
        for m in find_skills(name, include_weak=False):
            add(m.key, EvidenceRef("certification", cid, name, "user_provided", "certification"))
    # dedupe (type, id, status, via)
    for key, refs in idx.items():
        seen, out = set(), []
        for r in sorted(refs, key=lambda r: (r.status not in CLAIMABLE, bool(r.via), r.type)):
            k = (r.type, r.id, r.status in CLAIMABLE, r.via)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        idx[key] = out
    return idx


def match_requirements(reqs: list[RequirementInput], p: ProfileSnapshot) -> list[MatchResult]:
    idx = build_index(p)
    out = []
    for r in reqs:
        if r.kind == "skill" and r.canonical:
            out.append(_match_skill(r, p, idx))
        elif r.kind == "experience":
            out.append(_match_experience(r, p))
        elif r.kind == "education":
            out.append(_match_education(r, p))
        elif r.kind == "certification":
            out.append(_match_certification(r, p))
        elif r.kind == "domain":
            out.append(_match_domain(r, p))
        else:
            out.append(MatchResult(r.id, "needs_verification", "Review this requirement yourself.", []))
    return out


def _match_skill(r: RequirementInput, p: ProfileSnapshot, idx) -> MatchResult:
    refs = idx.get(r.canonical, [])
    direct = [x for x in refs if x.status in CLAIMABLE and not x.via]
    via = [x for x in refs if x.status in CLAIMABLE and x.via]
    inferred = [x for x in refs if x.status == "inferred"]
    name = display_name(r.canonical)
    if direct or via:
        used = direct or via
        where = _names(used)
        how = f" (through {via[0].via})" if not direct else ""
        return MatchResult(r.id, "strong", f"{name} is demonstrated in {where}{how}.", used[:6])
    listed = r.canonical in p.skills
    related: list[EvidenceRef] = []
    related_names: list[str] = []
    for rk in sorted(related_keys(r.canonical)):
        demonstrated = [x for x in idx.get(rk, []) if x.status in CLAIMABLE]
        if demonstrated:
            related += demonstrated
            related_names.append(display_name(rk))
        elif rk in p.skills:
            related.append(EvidenceRef("skill", p.skills[rk], display_name(rk), "user_provided", "listed skill"))
            related_names.append(display_name(rk))
    if listed:
        ev = [EvidenceRef("skill", p.skills[r.canonical], name, "user_provided", "listed in your skills")]
        return MatchResult(r.id, "partial", f"{name} is listed in your skills, but no project or experience "
                                            "demonstrates it yet.", ev + inferred[:3])
    if inferred:
        return MatchResult(r.id, "needs_verification", f"{name} may be used in {_names(inferred)}, but this was "
                                                       "inferred - confirm it in the project library.", inferred[:4])
    if related:
        return MatchResult(r.id, "partial", f"No direct {name} evidence; related experience: "
                                            f"{', '.join(related_names[:4])}.", related[:4])
    return MatchResult(r.id, "missing", f"No evidence of {name} in your profile or projects.", [])


def _names(refs: list[EvidenceRef]) -> str:
    names = list(dict.fromkeys(r.label for r in refs))
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:2]) + f" and {len(names) - 2} more"


def _match_experience(r: RequirementInput, p: ProfileSnapshot) -> MatchResult:
    m = re.search(r"(\d+(?:\.\d)?)", r.label)
    need = float(m.group(1)) if m else 0.0
    if need == 0:
        return MatchResult(r.id, "strong", "The role is open to entry-level candidates.", [])
    if p.years_of_experience is None:
        return MatchResult(r.id, "needs_verification", f"The posting asks for {r.label.lower()}. Add your years of "
                                                       "experience to your profile to check this.", [])
    have = float(p.years_of_experience)
    if have >= need:
        return MatchResult(r.id, "strong", f"You have {have:g} years; the posting asks for {need:g}+.", [])
    return MatchResult(r.id, "missing", f"You have {have:g} years; the posting asks for {need:g}+.", [])


_LEVELS = [
    (3, re.compile(r"ph\.?\s?d|doctor", re.I)),
    (2, re.compile(r"master|m\.?\s?tech|m\.?\s?e\b|m\.?\s?sc|\bms\b|m\.s\.|mca|mba|postgraduate", re.I)),
    (1, re.compile(r"bachelor|b\.?\s?tech|b\.?\s?e\b|b\.?\s?sc|\bbs\b|b\.s\.|bca|undergraduate|graduat|degree", re.I)),
]
_FIELD_RX = re.compile(r"computer science|computer engineering|information technology|software|\bcse\b|\bit\b|"
                       r"electronics|data science|artificial intelligence|mathematics|engineering", re.I)


def _level(text: str) -> int:
    for lvl, rx in _LEVELS:
        if rx.search(text):
            return lvl
    return 0


def _match_education(r: RequirementInput, p: ProfileSnapshot) -> MatchResult:
    if not p.educations:
        return MatchResult(r.id, "needs_verification", "Add your education to the profile to check this.", [])
    need = _level(r.label) or 1
    # "Bachelor's or Master's" means a bachelor's is enough
    if re.search(r"bachelor|b\.?\s?tech|b\.?e\b|undergrad", r.label, re.I):
        need = 1
    best_id, best_text, best = None, "", 0
    for eid, text in p.educations:
        lvl = _level(text)
        if lvl > best:
            best_id, best_text, best = eid, text, lvl
    ref = [EvidenceRef("education", best_id, best_text, "user_provided")] if best_id else []
    if best >= need:
        wants_field = _FIELD_RX.search(r.label)
        if wants_field and not _FIELD_RX.search(best_text):
            return MatchResult(r.id, "partial", "Your degree level matches, but the field of study may differ.", ref)
        return MatchResult(r.id, "strong", f"Your {best_text} meets this requirement.", ref)
    if best == 0:
        return MatchResult(r.id, "needs_verification", "The degree level in your profile could not be determined.", ref)
    return MatchResult(r.id, "missing", "The posting asks for a higher degree than your profile lists.", ref)


def _match_certification(r: RequirementInput, p: ProfileSnapshot) -> MatchResult:
    words = _cert_tokens(r.label)
    vendor = {m.key for m in find_skills(r.label, include_weak=False)}
    best, best_ref = 0.0, None
    same_vendor = []
    for cid, name in p.certifications:
        toks = _cert_tokens(name)
        overlap = len(words & toks) / max(1, len(words))
        if overlap > best:
            best, best_ref = overlap, EvidenceRef("certification", cid, name, "user_provided")
        if vendor & {m.key for m in find_skills(name, include_weak=False)}:
            same_vendor.append(EvidenceRef("certification", cid, name, "user_provided"))
    if best >= 0.7 and best_ref:
        return MatchResult(r.id, "strong", f"You hold {best_ref.label}.", [best_ref])
    if same_vendor:
        return MatchResult(r.id, "partial", f"You hold a related certification: {same_vendor[0].label}.", same_vendor)
    if not p.certifications:
        return MatchResult(r.id, "missing", "No certifications in your profile.", [])
    return MatchResult(r.id, "missing", "None of your certifications match this.", [])


def _cert_tokens(s: str) -> set[str]:
    stop = {"certified", "certification", "certificate", "is", "a", "plus", "the", "or", "and", "any", "preferred",
            "associate", "professional", "of", "in", "with", "–", "-"}
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in stop and len(w) > 1}


def _match_domain(r: RequirementInput, p: ProfileSnapshot) -> MatchResult:
    rx = re.compile(rf"\b{re.escape(r.label.lower())}\b", re.I)
    hits = [EvidenceRef("project", pr.id, pr.title, "verified", "mentioned in the project description")
            for pr in p.projects if rx.search(pr.description + " " + pr.readme[:3000])]
    hits += [EvidenceRef("experience", e.id, e.label, "user_provided") for e in p.experiences if rx.search(e.text)]
    if hits:
        return MatchResult(r.id, "partial", f"{r.label} appears in {_names(hits)}.", hits[:3])
    return MatchResult(r.id, "missing", f"No {r.label} domain work in your profile.", [])


def score_projects(reqs: list[RequirementInput], p: ProfileSnapshot) -> list[ProjectScore]:
    skill_reqs = [r for r in reqs if r.kind == "skill" and r.canonical]
    now = datetime.now(UTC)
    scores = []
    for proj in p.projects:
        ev: dict[str, tuple[str, str]] = {}  # canonical -> (best status, via)
        for canonical, status, _, _ in proj.evidence:
            if not canonical:
                continue
            _keep_best(ev, canonical, status, "")
            for parent in implied_keys(canonical):
                _keep_best(ev, parent, status, display_name(canonical))
        for b in proj.bullets:
            for m in find_skills(b, include_weak=False):
                _keep_best(ev, m.key, "user_provided", "")
        total, matched = 0.0, []
        for r in skill_reqs:
            if r.canonical not in ev:
                continue
            status, via = ev[r.canonical]
            factor = 1.0 if status in CLAIMABLE else 0.35
            if via:
                factor *= 0.6
            total += IMPORTANCE_WEIGHT[r.importance] * factor
            matched.append({"requirement_id": r.id, "label": r.label, "importance": r.importance,
                            "evidence_status": status, **({"via": via} if via else {})})
        if not matched:
            continue
        if proj.pushed_at and (now - proj.pushed_at).days < 365:
            total += 0.5
        if proj.bullets:
            total += 0.3
        scores.append(ProjectScore(proj.id, round(total, 2), matched))
    scores.sort(key=lambda s: -s.score)
    return scores


def _keep_best(ev: dict, key: str, status: str, via: str) -> None:
    cur = ev.get(key)
    rank = {"user_provided": 0, "verified": 0, "inferred": 1, "unsupported": 9}
    if status == "unsupported":
        return
    if cur is None or (rank[status], bool(via)) < (rank[cur[0]], bool(cur[1])):
        ev[key] = (status, via)
