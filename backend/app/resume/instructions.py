"""Job-specific resume instructions.

Instructions are stored on the job and apply to that job's resumes only; the master profile and settings are
never changed by them. They come from quick controls and from free-text lines, which are parsed into the same
structured controls. Anything asking CareerPilot to state something the evidence does not support is recorded
as a conflict and not applied: explicit instructions rank above template and JD preferences, but never above
verified facts.

Priority: verified evidence > explicit user instructions > template constraints > JD requirements > model output.
"""

import re
from dataclasses import dataclass, field

LINK_CHOICES = ("default", "include", "exclude")
SECTION_CHOICES = ("auto", "include", "exclude")
EMPHASIS_AREAS = {
    "backend": ("backend", "database", "concept", "devops", "security"),
    "frontend": ("frontend", "mobile"),
    "ml": ("ml", "data"),
    "data": ("data", "database"),
    "cloud": ("cloud", "devops"),
    "testing": ("testing",),
    "systems": ("concept", "language"),
}
DEFAULT_CONTROLS = {
    "portfolio": "default",
    "github": "default",
    "linkedin": "default",
    "length": "default",  # default | 1 | 2
    "projects": "auto",  # auto | 2 | 3 | 4
    "summary": "auto",
    "certifications": "auto",
    "template_id": None,
}


@dataclass
class Interpretation:
    controls: dict = field(default_factory=dict)
    project_priority: list[str] = field(default_factory=list)  # project names, highest first
    emphasis: dict[str, float] = field(default_factory=dict)  # area -> +1 / -1
    flags: set[str] = field(default_factory=set)  # keep_original_summary, relevant_skills_only, concise_summary...
    applied: list[str] = field(default_factory=list)  # human-readable interpretation of each understood line
    conflicts: list[dict] = field(default_factory=list)  # {"instruction", "reason", "options"}
    unrecognized: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    reuse_order_from: str = ""  # company name whose latest resume's project order should be reused


_NEG = r"(?:don'?t|do not|never|no|remove|drop|exclude|without|skip|omit|hide|leave out)"
_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "1": 1, "2": 2, "3": 3, "4": 4}
FABRICATION = re.compile(
    r"\b(say|claim|state|write|mention|add|show|pretend|make it look like|list)\b[^.]{0,40}\b(i have|i've|i had|i was|"
    r"experience (?:in|with)|years? of|worked (?:at|on|with)|certified|certification in|led|managed|increased|"
    r"reduced|improved)\b|\b\d+\+?\s*(years?|yrs?)\b.*\b(experience|exp)\b|\b(fake|invent|make up|exaggerate)\b",
    re.I,
)


def split_lines(text: str) -> list[str]:
    parts = re.split(
        r"\n+|(?<=[.;])\s+(?=[A-Z])|,\s*(?:and\s+)?(?=(?:include|exclude|keep|put|remove|use|add|drop|"
        r"make|emphasi[sz]e|focus|do not|don't|no)\b)",
        text or "",
        flags=re.I,
    )
    return [p.strip(" -•*\t.,") for p in parts if p and p.strip(" -•*\t.,")]


def parse(lines: list[str], project_names: list[str]) -> Interpretation:
    out = Interpretation()
    for raw in lines:
        line = raw.strip()
        if not line or re.fullmatch(r"(for|on|in) (this|the) (job|role|application|position)", line, re.I):
            continue
        low = line.lower()
        understood = False

        if FABRICATION.search(line) and not re.search(r"\b(emphasi[sz]e|highlight|focus|prominent|order|first)\b", low):
            out.conflicts.append(
                {
                    "instruction": line,
                    "reason": "This asks the resume to state something as fact. CareerPilot only states what your "
                    "verified evidence or your profile supports.",
                    "options": [
                        "Add verified experience to your profile",
                        "Keep it as a skill only if your evidence supports it",
                        "Create a supporting project",
                    ],
                }
            )
            continue

        for link, words in (
            ("portfolio", r"portfolio|personal (?:web)?site|website"),
            ("github", r"git ?hub"),
            ("linkedin", r"linked ?in"),
        ):
            if re.search(rf"\b({words})\b", low) and re.search(
                r"\b(link|url|header|top|contact|profile)\b|"
                rf"{_NEG}|include|add|keep|show",
                low,
            ):
                neg = re.search(rf"{_NEG}\b[^.]*\b({words})", low) or re.search(
                    rf"\b({words})\b[^.]*\b(removed|out)\b", low
                )
                out.controls[link] = "exclude" if neg else "include"
                out.applied.append(f"{link.capitalize()} link: {'exclude' if neg else 'include'}")
                understood = True

        m = re.search(
            r"\b(exactly |only |keep (?:it |the resume )?(?:to |at |within )?|max(?:imum)? |fit (?:in|on|to) )?"
            r"(one|two|1|2)[\s-]pages?\b",
            low,
        )
        if m:
            n = _NUM[m.group(2)]
            out.controls["length"] = n
            out.applied.append(f"Length: {n} page{'s' if n > 1 else ''}")
            understood = True

        m = re.search(
            r"\b(?:use |include |show |keep |only |limit to |max(?:imum)? (?:of )?)?(two|three|four|2|3|4)\s+"
            r"projects?\b",
            low,
        )
        if m:
            n = _NUM[m.group(1)]
            out.controls["projects"] = n
            out.applied.append(f"Projects: {n}")
            understood = True

        for section in ("summary", "certifications"):
            key = "certification" if section == "certifications" else "summary"
            if re.search(rf"\b{key}s?\b", low):
                if re.search(rf"{_NEG}\b[^.]*\b{key}", low):
                    out.controls[section] = "exclude"
                    out.applied.append(f"{section.capitalize()}: exclude")
                    understood = True
                elif section == "summary" and re.search(
                    r"\boriginal\b|\bsame\b|\bunchanged\b|\bdon'?t (?:change|rewrite)", low
                ):
                    out.flags.add("keep_original_summary")
                    out.applied.append("Summary: keep your original wording")
                    understood = True
                elif section == "summary" and re.search(r"\b(concise|short|brief|shorter)\b", low):
                    out.flags.add("concise_summary")
                    out.applied.append("Summary: concise")
                    understood = True
                elif re.search(r"\b(include|keep|add|show)\b", low):
                    out.controls[section] = "include"
                    out.applied.append(f"{section.capitalize()}: include")
                    understood = True

        # "Put Resolve above EventSphere", "Resolve first", "prioritize Resolve"
        mentioned = [n for n in project_names if n and re.search(rf"\b{re.escape(n.lower())}\b", low)]
        if mentioned and re.search(r"\b(first|top|above|before|prioriti[sz]e|lead with|highlight|then|order)\b", low):
            ordered = sorted(mentioned, key=lambda n: low.index(n.lower()))
            if re.search(r"\bbelow\b|\bafter\b", low) and len(ordered) == 2:
                ordered.reverse()
            for n in ordered:
                if n not in out.project_priority:
                    out.project_priority.append(n)
            out.applied.append("Project order: " + " → ".join(out.project_priority))
            understood = True
        elif re.search(r"\b(put|first|above|prioriti[sz]e)\b", low) and re.search(r"\bproject", low) and not mentioned:
            out.ambiguous.append(line)

        for area, _ in EMPHASIS_AREAS.items():
            words = {
                "backend": r"back[\s-]?end|server[\s-]side|api",
                "frontend": r"front[\s-]?end|ui|react",
                "ml": r"\bml\b|machine learning|\bai\b|deep learning",
                "data": r"\bdata\b|analytics",
                "cloud": r"cloud|devops|deployment",
                "testing": r"testing|\bqa\b",
                "systems": r"systems|low[\s-]level",
            }[area]
            if re.search(words, low) and re.search(
                r"emphasi[sz]|focus|prominent|highlight|stress|more|less|"
                r"de-?emphasi|don'?t|do not",
                low,
            ):
                neg = re.search(rf"(don'?t|do not|less|de-?emphasi[sz]e|avoid|minimi[sz]e)\b[^.]*({words})", low)
                out.emphasis[area] = -1.0 if neg else 1.0
                out.applied.append(f"{'De-emphasize' if neg else 'Emphasize'} {area}")
                understood = True

        if re.search(
            r"(don'?t|do not|avoid|no)\b[^.]*\b(technolog\w*|skills?|tools?)\b[^.]*\b(not|aren'?t|irrelevant|"
            r"unrelated|relevant)",
            low,
        ) or re.search(r"only (?:relevant|related) (?:skills|technolog)", low):
            out.flags.add("relevant_skills_only")
            out.applied.append("Skills: only those relevant to this job")
            understood = True
        m = re.search(
            r"same (?:project )?order(?:ing)? as (?:in )?(?:my )?(?:previous|last|earlier|old) (.+?) resume", low
        )
        if m:
            out.reuse_order_from = m.group(1).strip()
            out.applied.append(f"Project order: same as your previous {m.group(1).strip()} resume")
            understood = True
        if re.search(r"(don'?t|do not|never)\b[^.]*\bchange\b[^.]*\beducation", low):
            out.flags.add("keep_education")
            out.applied.append("Education: unchanged")
            understood = True
        if re.search(r"(keep|same|don'?t change|do not change)\b[^.]*\b(layout|template|format)", low):
            out.flags.add("template_locked")
            out.applied.append("Template: exactly as is")
            understood = True
        if (
            re.search(r"bullets?\b[^.]*\b(backend|technical)[\s-]?(focused|heavy)?", low)
            and "backend" not in out.emphasis
        ):
            out.emphasis["backend"] = 1.0
            out.applied.append("Emphasize backend")
            understood = True

        if not understood:
            out.unrecognized.append(line)
    return out


def merge(controls: dict, custom: list[str], project_names: list[str]) -> tuple[dict, Interpretation]:
    """Quick controls win over free-text for the same setting; free text fills the rest."""
    interp = parse(custom, project_names)
    merged = dict(DEFAULT_CONTROLS)
    merged.update({k: v for k, v in interp.controls.items() if k in merged})
    for k, v in (controls or {}).items():
        if k in merged and v not in (None, "", "default", "auto"):
            merged[k] = v
    return merged, interp


def effective_config(
    job_instructions: dict, settings, template_pages: int | None, project_names: list[str], template_name: str = ""
) -> dict:
    ji = job_instructions or {}
    controls, interp = merge(ji.get("controls") or {}, ji.get("custom") or [], project_names)
    master = {
        "portfolio": getattr(settings, "header_portfolio", "template"),
        "github": getattr(settings, "header_github", "template"),
        "linkedin": getattr(settings, "header_linkedin", "template"),
    }
    links = {}
    for k in ("portfolio", "github", "linkedin"):
        v = controls[k]
        links[k] = v if v in ("include", "exclude") else master[k]  # job override > master default > template
    length = controls["length"]
    default_len = getattr(settings, "resume_max_pages", None) or template_pages or 1
    max_pages = int(length) if length not in ("default", None) else int(default_len)
    projects = controls["projects"]
    return {
        "template_id": controls.get("template_id"),
        "template_name": template_name,
        "length": max_pages,
        "length_explicit": length not in ("default", None),
        "projects": int(projects) if projects not in ("auto", None) else getattr(settings, "max_projects", 3),
        "projects_explicit": projects not in ("auto", None),
        "links": links,
        "summary": controls["summary"],
        "certifications": controls["certifications"],
        "project_priority": interp.project_priority,
        "emphasis": interp.emphasis,
        "flags": sorted(interp.flags),
        "applied": interp.applied,
        "conflicts": interp.conflicts,
        "unrecognized": interp.unrecognized,
        "ambiguous": interp.ambiguous,
        "custom_count": len(ji.get("custom") or []),
        "reuse_order_from": interp.reuse_order_from,
    }


def summary_lines(cfg: dict) -> list[str]:
    """The "Your instructions" list shown before generation."""
    lines = []
    for k, label in (("portfolio", "Portfolio link"), ("github", "GitHub link"), ("linkedin", "LinkedIn link")):
        v = cfg["links"][k]
        if v != "template":
            lines.append(f"{'Include' if v == 'include' else 'Exclude'} {label}")
    if cfg["length_explicit"]:
        lines.append(f"Keep resume to {cfg['length']} page{'s' if cfg['length'] > 1 else ''}")
    if cfg["projects_explicit"]:
        lines.append(f"Use {cfg['projects']} projects")
    for s in ("summary", "certifications"):
        if cfg[s] != "auto":
            lines.append(f"{'Include' if cfg[s] == 'include' else 'Remove'} {s}")
    if cfg["project_priority"]:
        lines.append(
            "Put " + " before ".join(cfg["project_priority"]) + (" first" if len(cfg["project_priority"]) == 1 else "")
        )
    for area, w in cfg["emphasis"].items():
        lines.append(f"{'Emphasize' if w > 0 else 'De-emphasize'} {area}")
    for f in cfg["flags"]:
        lines.append(
            {
                "keep_original_summary": "Keep the original summary",
                "relevant_skills_only": "Only job-relevant skills",
                "keep_education": "Education unchanged",
                "concise_summary": "Concise summary",
                "template_locked": "Template exactly as is",
            }.get(f, f)
        )
    return list(dict.fromkeys(lines))
