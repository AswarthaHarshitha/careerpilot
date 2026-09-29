"""Site adapters for application portals.

The generic adapter reads any standard HTML form. Site adapters add what is known about a portal: how to reach
the form, whether an account sign-in is required (the user always does that part), what confirmation looks
like, and how much CareerPilot can do there. Support levels are stated plainly; no portal is claimed as fully
automated unless the form can be filled end to end.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Adapter:
    name: str
    label: str
    hosts: tuple[str, ...] = ()
    #: "form" - fill the form directly; "signin" - the user signs in first, then CareerPilot fills;
    #: "generic" - best effort on an unrecognised form
    support: str = "generic"
    apply_text: tuple[str, ...] = ()  # extra button texts that open the application form
    note: str = ""
    confirmation_id: re.Pattern = field(
        default=re.compile(
            r"(?:application|confirmation|reference|submission)\s*(?:id|number|no\.?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,40})",
            re.I,
        )
    )

    def matches(self, url: str) -> bool:
        host = re.sub(r"^https?://", "", url.lower()).split("/")[0]
        return any(host == h or host.endswith("." + h) for h in self.hosts)


GENERIC = Adapter(
    "generic", "Company career site", note="Standard HTML forms are filled; unusual widgets are left to you."
)

ADAPTERS: tuple[Adapter, ...] = (
    Adapter("greenhouse", "Greenhouse", ("greenhouse.io",), "form", ("Apply for this job",)),
    Adapter("lever", "Lever", ("lever.co",), "form", ("Apply for this job",)),
    Adapter("ashby", "Ashby", ("ashbyhq.com",), "form", ("Apply for this Job",)),
    Adapter(
        "smartrecruiters",
        "SmartRecruiters",
        ("smartrecruiters.com",),
        "form",
        ("I'm interested", "Apply"),
        "Multi-page form; each page is filled and continued.",
    ),
    Adapter(
        "workday",
        "Workday",
        ("myworkdayjobs.com", "myworkdaysite.com", "workday.com"),
        "signin",
        ("Apply", "Apply Manually"),
        "Workday needs an account for each employer. Sign in or create it in the browser window; CareerPilot fills "
        "the pages after that.",
    ),
    Adapter(
        "taleo",
        "Taleo",
        ("taleo.net",),
        "signin",
        ("Apply Online", "Apply"),
        "Taleo requires signing in on the employer's portal. CareerPilot continues after you sign in.",
    ),
    Adapter(
        "oracle",
        "Oracle Recruiting Cloud",
        ("oraclecloud.com",),
        "signin",
        ("Apply Now",),
        "Oracle's portal verifies your email before the form opens. Complete that step in the browser window.",
    ),
    Adapter(
        "successfactors",
        "SAP SuccessFactors",
        ("successfactors.com", "successfactors.eu", "sapsf.com"),
        "signin",
        ("Apply",),
        "SuccessFactors requires signing in. CareerPilot continues after you sign in.",
    ),
    Adapter(
        "icims",
        "iCIMS",
        ("icims.com",),
        "signin",
        ("Apply for this job online",),
        "iCIMS usually asks for an email or account first; complete that in the browser window.",
    ),
)


def adapter_for(url: str) -> Adapter:
    return next((a for a in ADAPTERS if a.matches(url)), GENERIC)


def confirmation_id(adapter: Adapter, text: str) -> str:
    m = adapter.confirmation_id.search(text or "")
    return m.group(1)[:120] if m else ""
