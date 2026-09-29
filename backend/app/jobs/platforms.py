"""Reads job postings from applicant-tracking systems and generic career pages.

Known ATS platforms publish job data through public JSON endpoints, which are more reliable than scraping
their pages. Everything else falls back to schema.org JobPosting data embedded in the page, then to the
page's main content.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from app.core.errors import ExternalFetchError
from app.core.fetch import fetch
from app.jobs.html import extract_json_ld_jobposting, html_to_text, main_content_text, page_meta


@dataclass
class RawPosting:
    platform: str
    job_url: str
    title: str = ""
    company: str = ""
    job_code: str = ""
    location: str = ""
    workplace_type: str = ""
    employment_type: str = ""
    description_text: str = ""
    application_url: str = ""
    posted_date: date | None = None
    deadline: date | None = None
    deadline_source: str = ""
    notes: list[str] = field(default_factory=list)


def detect_platform(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    path = urlsplit(url).path
    if host.endswith("greenhouse.io") or "gh_jid=" in url:
        return "greenhouse"
    if host == "jobs.lever.co" or host.endswith(".lever.co"):
        return "lever"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host.endswith("smartrecruiters.com"):
        return "smartrecruiters"
    if host.endswith("myworkdayjobs.com") or host.endswith("myworkdaysite.com"):
        return "workday"
    if host.endswith("oraclecloud.com") and "CandidateExperience" in path:
        return "oracle"
    if "taleo.net" in host:
        return "taleo"
    if host.endswith("icims.com"):
        return "icims"
    if "successfactors" in host or "/career?" in url and "company=" in url:
        return "successfactors"
    if re.search(r"/careers?/|/jobs?/", path):
        return "company_site"
    return "generic"


def fetch_posting(url: str) -> RawPosting:
    platform = detect_platform(url)
    try:
        if platform == "greenhouse":
            p = _greenhouse(url)
            if p:
                return p
        elif platform == "lever":
            p = _lever(url)
            if p:
                return p
        elif platform == "ashby":
            p = _ashby(url)
            if p:
                return p
        elif platform == "smartrecruiters":
            p = _smartrecruiters(url)
            if p:
                return p
        elif platform == "workday":
            p = _workday(url)
            if p:
                return p
    except ExternalFetchError:
        pass  # fall back to reading the page itself
    return _generic(url, platform)


# --- Greenhouse -----------------------------------------------------------------------------------


def _greenhouse(url: str) -> RawPosting | None:
    parts = urlsplit(url)
    m = re.search(r"/([\w-]+)/jobs/(\d+)", parts.path)
    board, job_id = (m.group(1), m.group(2)) if m else (None, None)
    if not job_id:
        q = parse_qs(parts.query)
        job_id = (q.get("gh_jid") or [None])[0]
        board = (q.get("for") or q.get("board") or [None])[0]
    if not (board and job_id):
        return None
    res = fetch(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true", accept="application/json"
    )
    d = json.loads(res.body)
    company = board
    try:
        meta = json.loads(fetch(f"https://boards-api.greenhouse.io/v1/boards/{board}", accept="application/json").body)
        company = meta.get("name") or board
    except (ExternalFetchError, ValueError):
        pass
    return RawPosting(
        platform="greenhouse",
        job_url=d.get("absolute_url") or url,
        title=d.get("title", ""),
        company=company,
        job_code=str(d.get("requisition_id") or d.get("internal_job_id") or d.get("id") or ""),
        location=(d.get("location") or {}).get("name", ""),
        description_text=html_to_text(d.get("content", "")),
        application_url=d.get("absolute_url") or url,
        posted_date=_date(d.get("first_published") or d.get("updated_at")),
    )


# --- Lever ----------------------------------------------------------------------------------------


def _lever(url: str) -> RawPosting | None:
    m = re.search(r"lever\.co/([\w.-]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    company, pid = m.groups()
    d = json.loads(fetch(f"https://api.lever.co/v0/postings/{company}/{pid}?mode=json", accept="application/json").body)
    parts = [d.get("descriptionPlain") or html_to_text(d.get("description", ""))]
    for lst in d.get("lists") or []:
        parts.append("## " + lst.get("text", ""))
        parts.append(html_to_text("<ul>" + lst.get("content", "") + "</ul>"))
    parts.append(d.get("additionalPlain") or html_to_text(d.get("additional", "")))
    cats = d.get("categories") or {}
    return RawPosting(
        platform="lever",
        job_url=d.get("hostedUrl") or url,
        title=d.get("text", ""),
        company=company.replace("-", " ").title(),
        job_code=d.get("id", ""),
        location=cats.get("location", ""),
        workplace_type=(d.get("workplaceType") or "").replace("onsite", "onsite"),
        employment_type=cats.get("commitment", ""),
        description_text="\n".join(p for p in parts if p),
        application_url=d.get("applyUrl") or url.rstrip("/") + "/apply",
        posted_date=_date_ms(d.get("createdAt")),
    )


# --- Ashby ----------------------------------------------------------------------------------------


def _ashby(url: str) -> RawPosting | None:
    m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    org, jid = m.groups()
    d = json.loads(fetch(f"https://api.ashbyhq.com/posting-api/job-board/{org}", accept="application/json").body)
    job = next((j for j in d.get("jobs", []) if j.get("id") == jid), None)
    if not job:
        return None
    return RawPosting(
        platform="ashby",
        job_url=job.get("jobUrl") or url,
        title=job.get("title", ""),
        company=org.replace("-", " ").title(),
        job_code=jid,
        location=job.get("location", ""),
        workplace_type="remote" if job.get("isRemote") else "",
        employment_type=job.get("employmentType", ""),
        description_text=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml", "")),
        application_url=job.get("applyUrl") or url.rstrip("/") + "/application",
        posted_date=_date(job.get("publishedAt")),
    )


# --- SmartRecruiters ------------------------------------------------------------------------------


def _smartrecruiters(url: str) -> RawPosting | None:
    m = re.search(r"smartrecruiters\.com/([^/]+)/(\d+)", url)
    if not m:
        return None
    company, pid = m.groups()
    d = json.loads(
        fetch(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}", accept="application/json").body
    )
    sections = (d.get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        sec = sections.get(key) or {}
        if sec.get("text"):
            parts.append("## " + (sec.get("title") or key))
            parts.append(html_to_text(sec["text"]))
    loc = d.get("location") or {}
    return RawPosting(
        platform="smartrecruiters",
        job_url=d.get("postingUrl") or url,
        title=d.get("name", ""),
        company=(d.get("company") or {}).get("name", company),
        job_code=d.get("refNumber") or pid,
        location=", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
        workplace_type="remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else ""),
        employment_type=(d.get("typeOfEmployment") or {}).get("label", ""),
        description_text="\n".join(parts),
        application_url=d.get("applyUrl") or url,
        posted_date=_date(d.get("releasedDate")),
    )


# --- Workday --------------------------------------------------------------------------------------


def _workday(url: str) -> RawPosting | None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    tenant = host.split(".")[0]
    segs = [s for s in parts.path.split("/") if s]
    if "job" not in segs:
        return None
    idx = segs.index("job")
    site_segs = segs[:idx]
    site = site_segs[-1] if site_segs else ""
    rest = "/".join(segs[idx + 1 :])
    if not (tenant and site and rest):
        return None
    api = f"https://{host}/wday/cxs/{tenant}/{site}/job/{rest}"
    d = json.loads(fetch(api, accept="application/json").body)
    info = d.get("jobPostingInfo") or {}
    org = (d.get("hiringOrganization") or {}).get("name") or tenant.upper()
    return RawPosting(
        platform="workday",
        job_url=info.get("externalUrl") or url,
        title=info.get("title", ""),
        company=org,
        job_code=info.get("jobReqId", ""),
        location=info.get("location", "") or ", ".join(info.get("additionalLocations") or []),
        employment_type=info.get("timeType", ""),
        description_text=html_to_text(info.get("jobDescription", "")),
        application_url=(info.get("externalUrl") or url).rstrip("/") + "/apply",
        posted_date=_date(info.get("startDate")),
        notes=["Workday applications require signing in to the company's Workday account."],
    )


# --- Generic page ---------------------------------------------------------------------------------


def _generic(url: str, platform: str) -> RawPosting:
    res = fetch(url)
    if "json" in res.content_type:
        raise ExternalFetchError(
            "The link returned data instead of a job page.", hint="Paste the job description instead."
        )
    orc = _oracle_from_page(res.text, res.url)
    if orc:
        return orc
    post = _parse_html(res.text, res.url, platform)
    if len(post.description_text) < 150:
        # Many career sites build the page with JavaScript. Render it in a sandboxed headless browser.
        from app.jobs.render import BrowserUnavailable, render_page

        try:
            html, final_url = render_page(res.url)
        except BrowserUnavailable:
            html = ""
        if html:
            rendered = _parse_html(html, final_url, platform)
            if len(rendered.description_text) > len(post.description_text):
                rendered.notes.append("The page was read after running its JavaScript.")
                post = rendered
    if len(post.description_text) < 150:
        raise ExternalFetchError(
            "The job page did not contain a readable job description.",
            hint="The site may require sign-in, block automated access, or use a portal CareerPilot cannot read yet.",
            actions=["open_manually", "paste_description", "retry"],
        )
    return post


def _parse_html(html: str, page_url: str, platform: str) -> RawPosting:
    soup = BeautifulSoup(html, "lxml")
    meta = page_meta(soup)
    ld = extract_json_ld_jobposting(soup)
    post = RawPosting(platform=platform, job_url=page_url)
    if ld:
        post.title = _s(ld.get("title"))
        org = ld.get("hiringOrganization")
        post.company = _s(org.get("name") if isinstance(org, dict) else org)
        post.description_text = html_to_text(_s(ld.get("description")))
        post.location = _ld_location(ld)
        if _s(ld.get("jobLocationType")).upper() == "TELECOMMUTE":
            post.workplace_type = "remote"
        et = ld.get("employmentType")
        post.employment_type = ", ".join(_employment(x) for x in (et if isinstance(et, list) else [et]) if x)
        ident = ld.get("identifier")
        if isinstance(ident, dict):
            post.job_code = _s(ident.get("value"))
        elif isinstance(ident, str | int):
            post.job_code = str(ident)
        post.posted_date = _date(_s(ld.get("datePosted")))
        vt = _date(_s(ld.get("validThrough")))
        if vt:
            post.deadline = vt
            post.deadline_source = f"Listing closes {vt.isoformat()} (validThrough in the posting's structured data)"
        post.application_url = _s(ld.get("url")) or ""
    body_text = main_content_text(soup)
    if len(post.description_text) < 200 and len(body_text) > len(post.description_text):
        post.description_text = body_text
    if not post.title:
        post.title = meta["h1"] or meta["og_title"] or meta["title"]
    if not post.company:
        post.company = meta["site_name"]
    post.application_url = post.application_url or _find_apply_link(soup, page_url) or page_url
    return post


# --- Oracle Recruiting Cloud (Oracle HCM Candidate Experience) ------------------------------------

_ORC_PATH = re.compile(
    r"/hcmUI/CandidateExperience/[a-z-]+/sites/(CX(?:_\d+)?)/(?:job|requisitions/preview)/(\d+)", re.I
)


def _oracle_from_page(html: str, page_url: str) -> RawPosting | None:
    """Oracle Recruiting Cloud pages are empty shells; the posting comes from the tenant's public REST API."""
    parts = urlsplit(page_url)
    m = _ORC_PATH.search(parts.path)
    if m:
        api_base, site, job_id = f"{parts.scheme}://{parts.netloc}", m.group(1), m.group(2)
    else:
        if "CandidateExperience" not in html:
            return None
        base = re.search(r'data-apibaseurl="(https://[\w.-]+\.oraclecloud\.com(?::\d+)?)"', html)
        site = re.search(r"siteNumber=(CX(?:_\d+)?)\b", html)
        job = re.search(r"/job/(\d+)", parts.path)
        if not (base and site and job):
            return None
        api_base, site, job_id = base.group(1), site.group(1), job.group(1)
    company = ""
    og = re.search(r'og:site_name"\s+content="([^"]+)"', html or "")
    if og:
        company = og.group(1)
    return _oracle(api_base, site, job_id, page_url, company)


def _oracle(api_base: str, site: str, job_id: str, page_url: str, company: str = "") -> RawPosting | None:
    api = (
        f"{api_base}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
        f'&finder=ById;Id="{job_id}",siteNumber={site}'
    )
    try:
        data = json.loads(fetch(api, accept="application/json").body)
    except (ExternalFetchError, ValueError):
        return None
    items = data.get("items") or []
    if not items:
        return None
    d = items[0]
    parts = []
    for title, key in (
        ("About the role", "ExternalDescriptionStr"),
        ("Responsibilities", "ExternalResponsibilitiesStr"),
        ("Qualifications", "ExternalQualificationsStr"),
    ):
        text = html_to_text(d.get(key) or "")
        if text and len(text) > 20:
            parts.append(f"## {title}\n{text}")
    locations = [d.get("PrimaryLocation") or ""] + [
        x.get("Name", "") for x in (d.get("secondaryLocations") or []) if isinstance(x, dict)
    ]
    workplace = (d.get("WorkplaceTypeCode") or d.get("WorkplaceType") or "").lower()
    workplace = (
        "remote"
        if "remote" in workplace
        else "hybrid"
        if "hybrid" in workplace
        else ("onsite" if "site" in workplace else "")
    )
    end = _date(d.get("ExternalPostedEndDate"))
    return RawPosting(
        platform="oracle",
        job_url=page_url,
        title=d.get("Title") or "",
        company=company or d.get("LegalEmployer") or "",
        job_code=str(d.get("Id") or job_id),
        location=" / ".join(x for x in locations if x),
        workplace_type=workplace,
        employment_type=d.get("JobSchedule") or d.get("WorkerType") or "",
        description_text="\n\n".join(parts),
        application_url=page_url,
        posted_date=_date(d.get("ExternalPostedStartDate")),
        deadline=end,
        deadline_source=f"Posting closes {end.isoformat()} (end date in the employer's job data)" if end else "",
        notes=["Oracle Recruiting Cloud applications ask you to verify your email before the form opens."],
    )


def _find_apply_link(soup: BeautifulSoup, base: str) -> str:
    from urllib.parse import urljoin

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()
        if re.fullmatch(r"(apply( now| for this (job|position|role))?|start application|submit application)", text):
            href = urljoin(base, a["href"])
            if href.startswith("http"):
                return href
    return ""


def _ld_location(ld: dict) -> str:
    loc = ld.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc] if loc else []
    out = []
    for item in locs:
        addr = item.get("address") if isinstance(item, dict) else None
        if isinstance(addr, dict):
            out.append(
                ", ".join(
                    _s(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry") if _s(addr.get(k))
                )
            )
        elif isinstance(addr, str):
            out.append(addr)
    return " / ".join(o for o in out if o)


_EMPLOYMENT = {
    "FULL_TIME": "Full-time",
    "PART_TIME": "Part-time",
    "CONTRACTOR": "Contract",
    "TEMPORARY": "Temporary",
    "INTERN": "Internship",
    "VOLUNTEER": "Volunteer",
    "PER_DIEM": "Per diem",
    "OTHER": "Other",
}


def _employment(v) -> str:
    raw = _s(v)
    return _EMPLOYMENT.get(raw.upper().replace("-", "_").replace(" ", "_"), raw)


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        return _s(v.get("name") or v.get("value") or "")
    return str(v).strip()


def _date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(v))
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _date_ms(v) -> date | None:
    try:
        return datetime.fromtimestamp(int(v) / 1000).date() if v else None
    except (TypeError, ValueError):
        return None
