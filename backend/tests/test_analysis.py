from datetime import date

from app.github.evidence import analyze_repository, is_negated_or_hypothetical, parse_dependencies
from app.jobs.parse import analyze_text, parse_explicit_date
from app.matching.engine import (
    ExperienceSnapshot,
    ProfileSnapshot,
    ProjectSnapshot,
    RequirementInput,
    match_requirements,
    score_projects,
)
from app.matching.taxonomy import canonicalize, find_skills
from tests.conftest import SAMPLE_JD, run_tasks

# --- taxonomy -------------------------------------------------------------------------------------


def test_aliases_resolve_to_one_skill():
    keys = [m.key for m in find_skills("Postgres, PostgreSQL, psql and ReactJS / React.js")]
    assert keys.count("postgresql") == 3 and keys.count("react") == 2


def test_ambiguous_single_letters_only_in_lists():
    assert {m.key for m in find_skills("Languages: Python, R, Go")} == {"python", "r", "go"}
    assert "go" not in {m.key for m in find_skills("Go to market quickly with AWS")}
    assert "r" not in {m.key for m in find_skills("R&D team")}


def test_canonicalize_unknown_skill_is_kept_not_guessed():
    assert canonicalize("Figma") == "x:figma"
    assert canonicalize("Spring Boot") == "spring_boot"


# --- job description analysis ---------------------------------------------------------------------


def test_requirements_classified_by_section():
    a = analyze_text(SAMPLE_JD)
    imp = {r.canonical: r.importance for r in a.requirements if r.kind == "skill"}
    assert imp["python"] == "required"
    assert imp["docker"] == "required"
    assert imp["fastapi"] == "required"  # named in the day-to-day responsibilities
    assert imp["aws"] == "preferred"
    assert imp["kubernetes"] == "preferred"


def test_extracts_job_facts():
    a = analyze_text(SAMPLE_JD)
    assert a.job_code == "REQ-20431"
    assert a.workplace_type == "hybrid"
    assert a.employment_type == "Full-time"
    assert a.min_years == 0
    assert "Bachelor" in a.education_text
    assert a.deadline == date(2026, 10, 15)
    assert "Apply by 15 October 2026" in a.deadline_source


def test_no_deadline_when_not_stated():
    a = analyze_text(SAMPLE_JD.replace("Apply by 15 October 2026.", ""))
    assert a.deadline is None and a.deadline_source == ""


def test_deadline_without_year_is_not_invented():
    a = analyze_text(SAMPLE_JD.replace("15 October 2026", "15th October"))
    assert a.deadline is None
    assert any("without a full date" in n for n in a.notes)


def test_explicit_date_parser_rejects_vague_dates():
    assert parse_explicit_date("next Friday") is None
    assert parse_explicit_date("October 2026") is None
    assert parse_explicit_date("Oct 3, 2026") == date(2026, 10, 3)
    assert parse_explicit_date("31 December 2026 at 11:59 PM IST") == date(2026, 12, 31)


def test_benefits_and_company_sections_do_not_create_requirements():
    a = analyze_text("## Benefits\n- Free Kubernetes training budget\n## Requirements\n- Python\n" + "x " * 80)
    assert "kubernetes" not in {r.canonical for r in a.requirements}


# --- repository evidence --------------------------------------------------------------------------


def test_dependencies_are_verified_evidence():
    pkg = '{"dependencies": {"react": "^18", "express": "^4"}, "devDependencies": {"vitest": "1"}}'
    assert set(parse_dependencies("package.json", pkg)) == {"react", "express", "vitest"}
    a = analyze_repository(
        description="", homepage="", topics=[], tree=[("package.json", 100)], readme="", manifests={"package.json": pkg}
    )
    verified = {e.canonical for e in a.evidence if e.status == "verified" and e.source_type == "dependency"}
    assert {"react", "express", "jest"} <= verified


def test_readme_negations_are_not_evidence():
    readme = (
        "# App\nBuilt with FastAPI and PostgreSQL.\n\nA layered monolith - no microservices, no queue.\n"
        "We deliberately did not add Celery/Redis.\n\n## Roadmap\n- Kubernetes deployment\n"
        "Setup script: `bootstrap_admin.py`."
    )
    a = analyze_repository(description="", homepage="", topics=[], tree=[], readme=readme, manifests={})
    keys = {e.canonical for e in a.evidence if e.source_type == "readme"}
    assert {"fastapi", "postgresql"} <= keys
    assert not keys & {"microservices", "celery", "redis", "kubernetes", "bootstrap"}


def test_negation_helper():
    t = "We use no microservices here."
    assert is_negated_or_hypothetical(t, t.index("micro"), t.index("micro") + 13)


def test_file_name_hints_are_only_inferred():
    a = analyze_repository(
        description="", homepage="", topics=[], tree=[("app/permissions.py", 10)], readme="", manifests={}
    )
    rbac = [e for e in a.evidence if e.canonical == "rbac"]
    assert rbac and all(e.status == "inferred" for e in rbac)


# --- matching -------------------------------------------------------------------------------------


def _profile():
    return ProfileSnapshot(
        skills={"python": 1, "sql": 2, "kubernetes": 3, "dsa": 4},
        experiences=[ExperienceSnapshot(1, "Intern at Example Labs", "Built REST APIs in Python with FastAPI")],
        projects=[
            ProjectSnapshot(
                10,
                "Taskboard",
                "Task tracker",
                [
                    ("fastapi", "verified", "FastAPI", "requirements.txt"),
                    ("postgresql", "verified", "PostgreSQL", "psycopg"),
                    ("docker", "inferred", "Docker", ""),
                ],
                [],
            ),
            ProjectSnapshot(11, "Gallery", "Photo site", [("react", "verified", "React", "package.json")], []),
        ],
        educations=[(1, "B.Tech Computer Science Example University")],
        years_of_experience=None,
    )


def _reqs():
    return [
        RequirementInput(1, "skill", "Python", "python", "required"),
        RequirementInput(2, "skill", "Kubernetes", "kubernetes", "preferred"),
        RequirementInput(3, "skill", "Docker", "docker", "required"),
        RequirementInput(4, "skill", "AWS", "aws", "preferred"),
        RequirementInput(5, "skill", "MySQL", "mysql", "required"),
        RequirementInput(6, "experience", "2+ years of experience", "", "required"),
        RequirementInput(7, "education", "Bachelor's degree in Computer Science", "", "required"),
    ]


def test_match_statuses_are_evidence_based():
    res = {m.requirement_id: m for m in match_requirements(_reqs(), _profile())}
    assert res[1].status == "strong"  # experience bullets + FastAPI implies Python
    assert res[2].status == "partial"  # only listed in skills
    assert "no project or experience" in res[2].rationale
    assert res[3].status == "needs_verification"  # only inferred evidence
    assert res[4].status == "missing"
    assert res[5].status == "partial"  # related skill (PostgreSQL) demonstrated
    assert res[6].status == "needs_verification"  # years not in profile
    assert res[7].status == "strong"


def test_project_ranking_prefers_verified_required_evidence():
    scores = score_projects(_reqs(), _profile())
    assert scores[0].project_id == 10
    assert all(s.project_id != 11 for s in scores)  # React is not asked for


def test_oracle_recruiting_cloud_postings_are_read_from_the_job_api(monkeypatch):
    import json as _json

    from app.core.fetch import FetchResult
    from app.jobs import platforms

    page = (
        '<html><head><meta property="og:site_name" content="Acme Corp"/></head><body>'
        '<base href="/en/sites/jobsearch" data-apibaseurl="https://acme.fa.us2.oraclecloud.com:443">'
        '<link href="https://acme.fa.us2.oraclecloud.com/hcmRestApi/CandidateExperience/x.png?siteNumber=CX_45001">'
        "<script>/* CandidateExperience */</script></body></html>"
    )
    api = {
        "items": [
            {
                "Id": "344317",
                "Title": "Application Software Engineer 1",
                "PrimaryLocation": "Bengaluru, India",
                "ExternalDescriptionStr": "<p>Build software with Java and SQL.</p>" * 5,
                "ExternalResponsibilitiesStr": "<ul><li>Develop reports using SQL</li></ul>",
                "ExternalQualificationsStr": "",
                "ExternalPostedEndDate": None,
            }
        ]
    }
    seen = []

    def fake_fetch(url, **kw):
        seen.append(url)
        if "hcmRestApi/resources" in url:
            return FetchResult(url, 200, "application/json", _json.dumps(api).encode(), {})
        return FetchResult(url, 200, "text/html", page.encode(), {})

    monkeypatch.setattr(platforms, "fetch", fake_fetch)
    p = platforms.fetch_posting("https://careers.acme.com/en/sites/jobsearch/job/344317?keyword=engineer")
    assert (p.platform, p.company, p.title, p.job_code) == (
        "oracle",
        "Acme Corp",
        "Application Software Engineer 1",
        "344317",
    )
    assert 'Id="344317",siteNumber=CX_45001' in seen[1] and seen[1].startswith("https://acme.fa.us2.oraclecloud.com")
    assert "Develop reports using SQL" in p.description_text
    assert p.deadline is None  # no end date in the data, so none is invented


AGGREGATOR_PAGE = """<!doctype html><html><head><meta property="og:site_name" content="Listing Hub">
<script type="application/ld+json">{"@type":"JobPosting","title":"Analyst","hiringOrganization":{"name":"Listing Hub"}}</script>
<script type="application/ld+json">{"@type":"JobPosting","title":"Associate Analyst","description":"<p>Associate Analyst role for freshers. Eligibility: any graduate. Location: Bengaluru. \
Responsibilities: data quality checks and reports. Skills: SQL, Excel and communication. Apply before the deadline.</p>","hiringOrganization":{"name":"Acme Corp "}}</script>
</head><body><article><h1>Acme Corp hiring Associate Analyst</h1>
<p>Acme Corp is hiring freshers. Apply using the official link below.</p>
<a href="https://whatsapp.com/channel/abc">Click here</a>
<a href="https://careers.acmecorp.com/jobs/12345/associate-analyst">Apply for job</a></article>
<aside><a href="/job/other-company-1/">Apply For This Job</a><a href="/job/other-company-2/">Apply For This Job</a>
<a href="/job/other-company-3/">Apply For This Job</a></aside></body></html>"""


def test_aggregator_pages_use_the_employer_link_not_other_listings():
    from app.jobs.platforms import _parse_html

    p = _parse_html(AGGREGATOR_PAGE, "https://listinghub.example/job/acme-associate-analyst/", "generic")
    assert p.from_aggregator
    assert p.company == "Acme Corp"  # the employer, not the listing site
    assert p.title == "Associate Analyst"
    assert p.application_url == "https://careers.acmecorp.com/jobs/12345/associate-analyst"
    assert "job listing site" in p.notes[0]


def test_aggregator_without_employer_link_asks_for_one():
    from app.jobs.platforms import _parse_html

    html = AGGREGATOR_PAGE.replace("https://careers.acmecorp.com/jobs/12345/associate-analyst", "/job/acme-more/")
    p = _parse_html(html, "https://listinghub.example/job/acme-associate-analyst/", "generic")
    assert p.from_aggregator and p.application_url == ""
    assert "Add the employer's application link" in p.notes[0]


def test_non_job_pages_are_rejected(auth_client):
    r = auth_client.post("/api/jobs", json={"description": "Follow this channel on WhatsApp for daily updates. " * 8})
    run_tasks()
    job = auth_client.get(f"/api/jobs/{r.json()['job']['id']}").json()
    assert job["analysis_state"] == "failed" and "doesn't look like a job posting" in job["analysis_error"]
