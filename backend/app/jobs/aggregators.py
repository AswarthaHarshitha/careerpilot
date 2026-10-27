"""Job aggregators: listing sites that repost jobs and link out to the employer.

On these pages "Apply" buttons usually lead to *other* jobs on the same site, while the employer's own
application page is an outbound link (often labelled "Apply for job", "Click" or the company name). Treating the
first "Apply" link as the application link sends the user - and the runner - to the wrong job. This module
recognises listing pages and picks the outbound employer link, or reports that none was found.
"""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

KNOWN_AGGREGATORS = (
    "commonjobs.in",
    "freshersworld.com",
    "fresherslive.com",
    "freshersnow.com",
    "offcampusjobs4u.com",
    "jobsforfreshers.co.in",
    "placementstore.com",
    "fresherscamp.com",
    "talentd.in",
    "jobsjugaad.com",
    "careerpower.in",
    "sarkariresult.com",
    "studentscircles.com",
    "freshershunt.in",
)
# Outbound links that are never an employer application page.
NOT_EMPLOYER = re.compile(
    r"whatsapp\.com|wa\.me|t\.me|telegram|instagram\.com|facebook\.com|twitter\.com|x\.com/|youtube\.com|youtu\.be|"
    r"linkedin\.com/(?:company|in|share|feed)|topmate\.io|maps\.google|google\.com/maps|play\.google|apps\.apple|"
    r"outskill|imp\.i\d+\.net|bit\.ly|amzn|doubleclick|googlesyndication|pinterest|reddit\.com|mailto:|tel:",
    re.I,
)
ATS_HOSTS = re.compile(
    r"greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com|myworkdaysite\.com|workday\.com|"
    r"taleo\.net|oraclecloud\.com|successfactors|icims\.com|zohorecruit|darwinbox|keka\.com|recruitee\.com|"
    r"freshteam|jobvite|bamboohr|breezy\.hr|teamtailor|personio|avature|eightfold|phenompeople|naukri\.com/job-listings",
    re.I,
)
APPLY_TEXT = re.compile(r"\b(apply|click|official|register|careers?|job link|link)\b", re.I)
JOB_PATH = re.compile(r"job|career|apply|requisition|opening|position|vacanc|jbid|jobid|req", re.I)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def is_aggregator(soup: BeautifulSoup, page_url: str) -> bool:
    host = _host(page_url)
    if any(host == h or host.endswith("." + h) for h in KNOWN_AGGREGATORS):
        return True
    # A page that links to several other jobs on the same site with "Apply" buttons is a listing, not a posting.
    same_site_apply = {
        urljoin(page_url, a["href"]).split("#")[0]
        for a in soup.find_all("a", href=True)
        if re.search(r"\bapply\b", a.get_text(" ", strip=True), re.I) and _host(urljoin(page_url, a["href"])) == host
    }
    same_site_apply.discard(page_url.split("#")[0])
    return len(same_site_apply) >= 3


def employer_links(soup: BeautifulSoup, page_url: str, company: str = "") -> list[str]:
    """Outbound links that are most likely the employer's own application page, best first."""
    site = _host(page_url)
    tokens = [t for t in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(t) >= 3 and t not in ("the", "ltd")]
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).strip()
        if not href.startswith("http") or NOT_EMPLOYER.search(href):
            continue
        host = _host(href)
        if not host or host == site or host.endswith("." + site):
            continue
        text = a.get_text(" ", strip=True)
        path = urlsplit(href).path + "?" + (urlsplit(href).query or "")
        score = 0
        if APPLY_TEXT.search(text):
            score += 3
        if any(t in host for t in tokens):
            score += 3
        if ATS_HOSTS.search(href):
            score += 3
        if JOB_PATH.search(path) and len(path) > 12:
            score += 2
        if path.strip("/?") == "":
            score -= 3  # a bare homepage is not an application page
        if score >= 4:
            scored[href] = max(score, scored.get(href, 0))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: -kv[1])]
