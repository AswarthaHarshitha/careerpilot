"""Maps application-form fields to the career profile.

Deterministic first: labels are normalised and matched against known field meanings. Values come only from
the profile, saved answers or the generated resume. Nothing is guessed:

* unknown fields              -> "needs_input"
* legal / consent questions   -> proposed from the profile but always "needs_confirmation"
* self-identification (EEO)   -> never filled automatically; "needs_input" unless the user saved an answer, and
                                 even then "needs_confirmation" for each application
* free-text questions         -> a draft (if a model is configured) marked "needs_confirmation"
"""

import re
from dataclasses import dataclass

# canonical key -> label pattern
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("resume", re.compile(r"\b(resume|résumé|cv|curriculum vitae)\b", re.I)),
    ("cover_letter", re.compile(r"cover\s*letter", re.I)),
    ("first_name", re.compile(r"\b(first|given|fore)\s*name\b|^fname$", re.I)),
    ("last_name", re.compile(r"\b(last|family|sur)\s*name\b|^surname$|^lname$", re.I)),
    ("preferred_name", re.compile(r"preferred\s*(first\s*)?name|nick\s*name", re.I)),
    ("full_name", re.compile(r"^(full\s*|your\s*|legal\s*)?name\b|^name\s*\*?$|full legal name", re.I)),
    ("email", re.compile(r"e-?mail", re.I)),
    ("phone", re.compile(r"phone|mobile|contact number|telephone|cell", re.I)),
    ("linkedin", re.compile(r"linked\s*in", re.I)),
    ("github", re.compile(r"git\s*hub", re.I)),
    ("portfolio", re.compile(r"portfolio|personal (web)?site|website|blog|other (url|link)|\burl\b", re.I)),
    ("current_company", re.compile(r"current (company|employer)|present employer|^company$|organi[sz]ation", re.I)),
    ("current_title", re.compile(r"current (job )?title|current (role|position)|^title$", re.I)),
    ("school", re.compile(r"school|university|college|institution", re.I)),
    ("degree", re.compile(r"\bdegree\b|qualification", re.I)),
    ("discipline", re.compile(r"discipline|major|field of study|specialization|branch", re.I)),
    (
        "graduation_year",
        re.compile(r"graduat\w*\s*(year|date)|year of (graduation|passing)|passing year|class of", re.I),
    ),
    ("gpa", re.compile(r"\b(c?gpa|grade point|cpi|percentage|aggregate)\b", re.I)),
    (
        "years_experience",
        re.compile(
            r"years? of (relevant |professional |work )?experience|how many years|"
            r"total experience|experience \(years\)",
            re.I,
        ),
    ),
    ("sponsorship", re.compile(r"sponsor|visa (support|status)|h-?1b", re.I)),
    (
        "work_authorization",
        re.compile(
            r"(legally )?(authori[sz]ed|eligible|permitted|entitled) to work|work "
            r"(authori[sz]ation|permit|eligibility)|right to work",
            re.I,
        ),
    ),
    ("notice_period", re.compile(r"notice period|how soon can you (join|start)|joining time", re.I)),
    (
        "start_date",
        re.compile(
            r"(earliest|available|availability|expected) (start|joining)|start date|when can you "
            r"start|date available",
            re.I,
        ),
    ),
    ("salary", re.compile(r"salary|compensation|ctc|pay expectation|expected pay|desired pay", re.I)),
    ("relocate", re.compile(r"relocat", re.I)),
    (
        "location",
        re.compile(
            r"current (city|location)|^city$|^location$|where are you (located|based)|your location|"
            r"^address",
            re.I,
        ),
    ),
    ("country", re.compile(r"^country|country of residence", re.I)),
    ("how_heard", re.compile(r"how did you (hear|find|learn)|where did you (hear|find)|source|referr?al source", re.I)),
    ("referral", re.compile(r"referred by|referrer|employee referral", re.I)),
    (
        "why_company",
        re.compile(
            r"why (do you want|are you interested|this (company|role)|us|join|would you like)|"
            r"what interests you|motivat",
            re.I,
        ),
    ),
    ("additional_info", re.compile(r"additional information|anything else|cover note|message to", re.I)),
]
SENSITIVE: list[tuple[str, re.Pattern]] = [
    ("gender", re.compile(r"gender|sex\b|pronoun", re.I)),
    ("race", re.compile(r"\brace\b|ethnic|hispanic|latin[oa]|caste", re.I)),
    ("veteran", re.compile(r"veteran|military|armed forces|protected veteran", re.I)),
    ("disability", re.compile(r"disabilit|handicap|impairment", re.I)),
    ("sexual_orientation", re.compile(r"sexual orientation|lgbt", re.I)),
    ("age", re.compile(r"date of birth|\bdob\b|\bage\b|birth ?date", re.I)),
    ("religion", re.compile(r"religio|faith", re.I)),
    ("criminal", re.compile(r"criminal|convict|felony|background check", re.I)),
    ("marital", re.compile(r"marital", re.I)),
]
CONSENT = re.compile(
    r"i (agree|consent|acknowledge|certify|confirm|understand)|privacy (policy|notice)|terms (of|and)|"
    r"data (processing|protection)|gdpr|accurate (and|&) complete|by (checking|submitting)",
    re.I,
)
LEGAL_KEYS = {"work_authorization", "sponsorship"}
FREE_TEXT_KEYS = {"why_company", "additional_info", "cover_letter"}


@dataclass
class FieldInput:
    field_ref: str
    label: str
    input_type: str  # text|email|tel|url|number|textarea|select|radio|checkbox|file|date|combobox
    required: bool = False
    options: list[str] | None = None
    step: int = 0
    has_value: bool = False  # the page already shows a value (a default such as a country code)


@dataclass
class Mapped:
    field_key: str
    value: str
    source: str  # profile | resume | saved_answer | draft | none
    status: str  # filled | needs_input | needs_confirmation | skipped
    sensitive: bool
    note: str = ""


def normalise_label(label: str) -> str:
    s = re.sub(r"[*: ]+", " ", label or "")
    s = re.sub(r"\(required\)|\(optional\)", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


CAPTCHA_LABEL = re.compile(r"captcha|image text|characters (shown|in the image)|verification code|security code", re.I)


def classify(label: str, input_type: str) -> tuple[str, bool]:
    text = normalise_label(label)
    if CAPTCHA_LABEL.search(text):
        return "captcha", False
    for key, rx in SENSITIVE:
        if rx.search(text):
            return key, True
    if input_type == "checkbox" and CONSENT.search(text):
        return "consent", False
    if input_type == "file":
        if re.search(r"cover", text, re.I):
            return "cover_letter", False
        if re.search(
            r"photo|picture|image|avatar|headshot|selfie|signature|id proof|aadhaar|passport|pan card", text, re.I
        ):
            return "", False  # never put the resume into a photo or document upload
        if re.search(r"resume|cv|curriculum|biodata", text, re.I) or re.fullmatch(
            r"\(unlabelled field\)|upload|attach\w*|file|document|browse", text.strip(), re.I
        ):
            return "resume", False
        return "", False
    for key, rx in PATTERNS:
        if rx.search(text):
            if key == "full_name" and re.search(r"first|last|company|school|university|preferred", text, re.I):
                continue
            if key in TEXT_ONLY_KEYS and input_type not in ("text", "email", "tel", "url", "textarea", "number", ""):
                continue  # e.g. a title/salutation dropdown sitting in the "First Name" row
            return key, False
    return "", False


TEXT_ONLY_KEYS = {
    "first_name",
    "last_name",
    "preferred_name",
    "full_name",
    "email",
    "phone",
    "linkedin",
    "github",
    "portfolio",
}


def _yes_no(v: bool | None) -> str:
    return "" if v is None else ("Yes" if v else "No")


def pick_option(value: str, options: list[str]) -> str | None:
    """Chooses the option that corresponds to a value; None when no option clearly matches."""
    if not options or not value:
        return None
    v = value.strip().lower()
    clean = [(o, o.strip().lower()) for o in options if o and o.strip()]
    for o, lo in clean:
        if lo == v:
            return o
    if v in ("yes", "no"):
        for o, lo in clean:
            if re.match(rf"^{v}\b", lo):
                return o
        return None
    for o, lo in clean:
        if v in lo or (len(lo) > 3 and lo in v):
            return o
    return None


def map_field(f: FieldInput, ctx: dict) -> Mapped:
    """ctx keys: profile (dict of values), saved_answers {key: (answer, sensitive)}, resume_version_id, draft_fn."""
    if classify(f.label, f.input_type)[0] == "captcha":
        return Mapped(
            "captcha",
            "",
            "none",
            "browser_only",
            False,
            "A CAPTCHA: type it in the browser window. CareerPilot never fills it.",
        )
    m = _map_field(f, ctx)
    if m.status == "needs_input" and not m.sensitive and not m.field_key and f.input_type in ("select", "combobox"):
        text = normalise_label(f.label)
        part_of = next((k for k, rx in PATTERNS if k in TEXT_ONLY_KEYS and rx.search(text)), None)
        if part_of:
            # e.g. the country-code or salutation picker inside the phone or name row
            return Mapped(
                "",
                "",
                "none",
                "skipped",
                False,
                f"Kept what the page already shows (part of the {part_of.replace('_', ' ')} field).",
            )
    if m.status == "needs_input" and not m.sensitive and not m.field_key and f.has_value:
        # nothing to say about it, and the page already holds a value: leave the page's choice alone
        return Mapped("", "", "none", "skipped", False, "Kept what the page already shows.")
    return m


def _map_field(f: FieldInput, ctx: dict) -> Mapped:
    key, sensitive = classify(f.label, f.input_type)
    p = ctx.get("profile", {})
    saved = ctx.get("saved_answers", {})

    if sensitive:
        if key in saved and saved[key][0]:
            value = saved[key][0]
            if f.options:
                value = pick_option(value, f.options) or ""
            if value:
                return Mapped(
                    key,
                    value,
                    "saved_answer",
                    "needs_confirmation",
                    True,
                    "Self-identification question - confirm your saved answer for this application.",
                )
        return Mapped(
            key,
            "",
            "none",
            "needs_input",
            True,
            "Self-identification question - CareerPilot never answers this for you.",
        )

    if key == "consent":
        return Mapped(
            "consent",
            "",
            "none",
            "needs_confirmation",
            False,
            "Read and accept this yourself; CareerPilot does not agree to terms on your behalf.",
        )

    if key == "resume":
        rv = ctx.get("resume_version_id")
        if rv:
            return Mapped("resume", f"resume_version:{rv}", "resume", "filled", False, ctx.get("resume_label", ""))
        return Mapped("resume", "", "none", "needs_input", False, "Generate or choose a resume version first.")

    value, source, status, note = "", "none", "needs_input", ""
    simple = {
        "first_name": p.get("first_name"),
        "last_name": p.get("last_name"),
        "full_name": p.get("full_name"),
        "preferred_name": p.get("first_name"),
        "email": p.get("email"),
        "phone": p.get("phone"),
        "linkedin": p.get("linkedin_url"),
        "github": p.get("github_url"),
        "portfolio": p.get("portfolio_url"),
        "location": p.get("location"),
        "school": p.get("school"),
        "degree": p.get("degree"),
        "discipline": p.get("discipline"),
        "graduation_year": p.get("graduation_year"),
        "gpa": p.get("gpa"),
        "notice_period": p.get("notice_period"),
        "salary": p.get("salary_expectation"),
        "start_date": p.get("earliest_start_date"),
        "current_company": p.get("current_company"),
        "current_title": p.get("current_title"),
        "country": p.get("country"),
    }
    if key in simple and simple[key]:
        value, source, status = str(simple[key]), "profile", "filled"
    elif key == "years_experience" and p.get("years_of_experience") is not None:
        value, source, status = f"{float(p['years_of_experience']):g}", "profile", "filled"
    elif key == "work_authorization" and p.get("work_authorization"):
        value, source, status = p["work_authorization"], "profile", "needs_confirmation"
        note = "Legal question - confirm the answer applies to this job's location."
    elif key == "sponsorship" and p.get("requires_sponsorship") is not None:
        value, source, status = _yes_no(p["requires_sponsorship"]), "profile", "needs_confirmation"
        note = "Legal question - confirm before submitting."
    elif key == "relocate" and p.get("willing_to_relocate") is not None:
        value, source, status = _yes_no(p["willing_to_relocate"]), "profile", "filled"
    elif key in saved and saved[key][0]:
        value, source, status = saved[key][0], "saved_answer", "filled"
    elif key in FREE_TEXT_KEYS or (f.input_type == "textarea" and not key):
        draft = ctx.get("draft_fn")
        text = draft(f.label) if draft else ""
        if text:
            value, source, status = text, "draft", "needs_confirmation"
            note = "Drafted from your verified profile - review and edit before submitting."
        else:
            note = "Write this answer yourself" + (" (configure an AI provider to get a draft)." if not draft else ".")
    elif key in LEGAL_KEYS:
        note = "Add this to your profile (Application preferences) or answer it here."

    if value and f.options and f.input_type in ("select", "radio", "combobox"):
        chosen = pick_option(value, f.options)
        if chosen is None:
            note = f"Your profile says “{value[:60]}”, which matches none of the options."
            value, status = "", "needs_input"
        else:
            value = chosen
    if f.input_type == "checkbox" and key not in ("relocate",) and not value:
        status = "needs_input"
    if not value and status == "filled":
        status = "needs_input"
    if not f.required and status == "needs_input" and not key:
        status, note = "skipped", "Optional and not in your profile - left blank."
    return Mapped(key, value, source, status, False, note)


def profile_context(profile, educations, experiences) -> dict:
    """Flattens profile rows into the values the mapper uses."""
    edu = educations[0] if educations else None
    cur = next((e for e in experiences if e.is_current), None)
    ctx = {
        "first_name": profile.first_name if profile else "",
        "last_name": profile.last_name if profile else "",
        "full_name": profile.full_name if profile else "",
        "email": profile.email if profile else "",
        "phone": profile.phone if profile else "",
        "linkedin_url": profile.linkedin_url if profile else "",
        "github_url": profile.github_url if profile else "",
        "portfolio_url": profile.portfolio_url if profile else "",
        "location": profile.location if profile else "",
        "work_authorization": profile.work_authorization if profile else "",
        "requires_sponsorship": profile.requires_sponsorship if profile else None,
        "willing_to_relocate": profile.willing_to_relocate if profile else None,
        "notice_period": profile.notice_period if profile else "",
        "salary_expectation": profile.salary_expectation if profile else "",
        "earliest_start_date": profile.earliest_start_date if profile else "",
        "years_of_experience": float(profile.years_of_experience)
        if profile and profile.years_of_experience is not None
        else None,
        "school": edu.institution if edu else "",
        "degree": edu.degree if edu else "",
        "discipline": edu.field_of_study if edu else "",
        "graduation_year": str(edu.graduation_year) if edu and edu.graduation_year else "",
        "gpa": edu.grade if edu else "",
        "current_company": cur.organization if cur else "",
        "current_title": cur.title if cur else "",
    }
    if profile and profile.full_name and not ctx["first_name"]:
        parts = profile.full_name.split()
        ctx["first_name"], ctx["last_name"] = parts[0], " ".join(parts[1:])
    return ctx
