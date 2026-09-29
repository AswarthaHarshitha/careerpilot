"""Plans for projects that would close a skill gap.

A suggested project is a plan, never experience: it is stored as NOT YET BUILT, it is never used in resumes,
and it only becomes evidence after the user links a real repository whose scan shows verified evidence for
the gap skills (see app.services.github_scan.verify_suggested_projects).
"""

import logging

from app.llm.provider import LLMError, LLMProvider, wrap_untrusted
from app.matching.taxonomy import category_of, display_name

log = logging.getLogger(__name__)

IDEAS = {
    "cloud": (
        "{anchor} service deployed on {gap}",
        "A production-style API that runs on {gap}: containerised or serverless compute, managed storage, "
        "secrets in the provider's secret store, and infrastructure defined as code.",
    ),
    "devops": (
        "Delivery pipeline for a {anchor} service",
        "Take a small {anchor} service from commit to running deployment with {gap}: build, test, package, "
        "deploy and roll back automatically.",
    ),
    "ml": (
        "{gap} assistant over technical documents",
        "A service that answers questions over a document collection using {gap}, with retrieval, citations, an "
        "evaluation set and a regression check for answer quality.",
    ),
    "data": (
        "{gap} pipeline for public datasets",
        "An end-to-end data pipeline using {gap}: ingestion from a public source, validation, transformation, "
        "a queryable model and a small dashboard.",
    ),
    "frontend": (
        "{gap} dashboard for a {anchor} API",
        "A responsive {gap} front end with authentication, data fetching, loading/error states and "
        "component tests, backed by a {anchor} API.",
    ),
    "backend": (
        "{gap} REST service with authentication",
        "A {gap} REST API with JWT authentication, role-based access, validation, pagination, migrations "
        "and integration tests.",
    ),
    "database": (
        "{gap}-backed feature for a {anchor} app",
        "Add a feature that genuinely needs {gap} (caching, search or document storage) to a {anchor} "
        "service, with measurements before and after.",
    ),
    "testing": (
        "{gap} test suite for a web application",
        "A maintainable automated test suite using {gap}: page objects, fixtures, CI execution and reports.",
    ),
    "language": (
        "Command-line tool in {gap}",
        "A useful CLI written in {gap} with argument parsing, file/network I/O, error handling, packaging "
        "and unit tests.",
    ),
    "default": (
        "{gap} project",
        "A focused project that exercises {gap} end to end, with tests, documentation and a deployment.",
    ),
}


def _idea_category(keys: list[str]) -> str:
    cats = [category_of(k) for k in keys]
    for c in ("cloud", "devops", "ml", "data", "frontend", "backend", "database", "testing", "language"):
        if c in cats:
            return c
    if "mobile" in cats:
        return "frontend"
    return "default"


COMPATIBLE = {
    "cloud": {"cloud", "devops", "database", "backend"},
    "devops": {"devops", "cloud", "testing"},
    "ml": {"ml", "data"},
    "data": {"data", "ml", "database"},
    "backend": {"backend", "database", "security", "concept"},
    "database": {"database", "backend", "data"},
    "frontend": {"frontend", "mobile", "testing"},
    "testing": {"testing", "devops"},
    "language": {"language", "backend", "concept"},
}


def coherent_gaps(gaps: list[str], limit: int = 3) -> list[str]:
    """Picks up to `limit` gap skills that belong together in one real project, most important first.
    Never tries to cram every missing keyword into a single project."""
    if not gaps:
        return []
    # concrete technologies make a buildable project; broad concepts ("system design") ride along
    concrete = [g for g in gaps if category_of(g) not in ("concept", "security", "tool")]
    gaps = concrete + [g for g in gaps if g not in concrete]
    primary = gaps[0]
    allowed = COMPATIBLE.get(category_of(primary), {category_of(primary)})
    chosen = [primary]
    for g in gaps[1:]:
        if len(chosen) >= limit:
            break
        if category_of(g) in allowed:
            chosen.append(g)
    return chosen


DEPTH = {
    "cloud": [
        "Infrastructure as code for every cloud resource",
        "Least-privilege IAM roles and secrets in the provider's secret manager",
        "Health checks, structured logs and an alarm on errors",
    ],
    "devops": [
        "Pipeline stages: lint, test, build image, deploy, smoke test",
        "Rollback on failed smoke test",
        "Container image scanning and pinned base images",
    ],
    "ml": [
        "Reproducible training with a fixed data split and recorded metrics",
        "An evaluation script whose numbers you can quote honestly",
        "A small inference API with input validation",
    ],
    "data": [
        "Idempotent loads with data-quality checks",
        "A documented schema and lineage",
        "Scheduled runs with failure alerts",
    ],
    "backend": [
        "Authentication, role-based authorization and per-user data isolation",
        "Database migrations and pagination",
        "Background job for slow work",
        "Consistent error responses",
    ],
    "database": [
        "Schema design with indexes justified by queries",
        "Measured query timings before/after",
        "Backups/restore documented",
    ],
    "frontend": [
        "Accessible components with loading, empty and error states",
        "Client-side routing and data caching",
        "Component and end-to-end tests",
    ],
    "testing": ["Page objects and fixtures", "Tests running in CI with reports", "Flaky-test handling"],
    "language": ["Clear module structure and packaging", "Unit tests and type checks", "A README with usage examples"],
}


def deterministic_plan(gaps: list[str], anchors: list[str], role: str = "", company: str = "") -> dict:
    gap_names = [display_name(k) for k in gaps]
    anchor_names = [display_name(k) for k in anchors] or ["Python"]
    cat = _idea_category(gaps)
    title_t, summary_t = IDEAS[cat]
    gap_str = " + ".join(gap_names[:3])
    anchor = anchor_names[0]
    title = title_t.format(gap=gap_str, anchor=anchor)
    stack = list(dict.fromkeys(gap_names + anchor_names[:3] + ["PostgreSQL" if cat in ("backend", "cloud") else ""]))
    stack = [s for s in stack if s]
    features = DEPTH.get(cat, [])[:3] + [
        f"Core workflow that cannot work without {gap_names[0]}",
        "Authentication and per-user data isolation",
        "Input validation with clear error messages",
        "Health check endpoint and structured logging",
        "Seed-free setup: the app starts empty and works with real input",
    ]
    if cat == "cloud":
        features.insert(1, f"Deployment to {gap_str} with infrastructure as code")
    if cat == "ml":
        features.insert(1, "Evaluation set with automated quality checks")
    plan = {
        "title": title,
        "summary": summary_t.format(gap=gap_str, anchor=anchor),
        "demonstrates": gap_names,
        "builds_on": anchor_names[:3],
        "for_role": " at ".join(x for x in (role, company) if x),
        "stack": stack,
        "architecture": [
            f"{anchor} application layer with a clear API boundary",
            f"{gap_names[0]} used for the part of the system it is best suited to",
            "PostgreSQL (or the store the job uses) for persistent state, with migrations",
            "Configuration from environment variables; secrets never committed",
        ],
        "features": features,
        "phases": [
            {
                "name": "1. Skeleton",
                "tasks": [
                    "Repository, README, licence, .gitignore, .env.example",
                    "Minimal app with a health check and one test",
                    "CI running tests",
                ],
            },
            {
                "name": "2. Core feature",
                "tasks": [
                    f"Implement the main workflow using {gap_names[0]}",
                    "Validation and error handling",
                    "Unit and integration tests",
                ],
            },
            {
                "name": "3. Production readiness",
                "tasks": [
                    "Authentication and authorization",
                    "Logging and metrics",
                    "Security review of inputs and secrets",
                ],
            },
            {
                "name": "4. Deployment",
                "tasks": [
                    f"Deploy using {gap_str if cat in ('cloud', 'devops') else 'a free tier host'}",
                    "Document the live URL and how to reproduce the deployment",
                ],
            },
        ],
        "tests": [
            "Unit tests for business rules",
            "API tests for success and failure paths",
            "Authorization tests (one user cannot read another's data)",
            f"A test that exercises the {gap_names[0]} integration",
        ],
        "deployment": [
            f"Target: {gap_str}" if cat in ("cloud", "devops") else "Any free tier that runs containers",
            "Environment variables documented in .env.example",
            "Health check wired to the platform",
        ],
        "repo_structure": "\n".join(
            [
                "README.md",
                ".env.example",
                ".github/workflows/ci.yml",
                "src/",
                "  api/",
                "  services/",
                "  models/",
                "tests/",
                "infra/" if cat in ("cloud", "devops") else "docs/",
            ]
        ),
        "readme_outline": [
            "What it does",
            "Architecture",
            "Tech stack",
            "Local setup",
            "Configuration",
            "Testing",
            "Deployment",
            "Design decisions",
        ],
        "github_setup": [
            "Public repository with a descriptive name and topic tags",
            "Repository description and homepage set to the live URL",
            "CI badge in the README; protected main branch",
        ],
    }
    plan["why"] = (
        f"The job asks for {', '.join(gap_names)} and none of your finished projects demonstrates "
        f"{'it' if len(gap_names) == 1 else 'them'}. This project builds on what you already use "
        f"({', '.join(anchor_names[:3])}) so it is realistic to finish and easy to discuss in an interview."
    )
    plan["interview_points"] = [
        f"Why {gap_names[0]} was the right tool for this part of the system, and what you would do differently",
        "One bug or failure you hit and how you diagnosed it",
        "How you tested it and what the tests do not cover",
        "What it would take to run this for real users",
    ]
    plan["coding_prompt"] = coding_prompt(plan)
    return plan


def coding_prompt(plan: dict) -> str:
    phases = "\n".join(f"- {p['name']}: " + "; ".join(p["tasks"]) for p in plan["phases"])
    return (
        f'Build a project called "{plan["title"]}".\n\n'
        f"Goal: {plan['summary']}\n\n"
        f"It must genuinely use: {', '.join(plan['demonstrates'])}. It builds on: {', '.join(plan['builds_on'])}.\n"
        f"Stack: {', '.join(plan['stack'])}.\n\n"
        "Features:\n" + "\n".join(f"- {f}" for f in plan["features"]) + "\n\n"
        "Work in phases, running the tests and committing after each:\n" + phases + "\n\n"
        "Tests required:\n" + "\n".join(f"- {t}" for t in plan["tests"]) + "\n\n"
        "Constraints: no hard-coded secrets; no fake or seeded demo data; a README that describes only what exists; "
        "clear error handling; a working deployment with its URL in the repository description."
    )


LLM_SYSTEM = (
    "Design one realistic portfolio project a candidate can build in 1-3 weeks to genuinely learn and demonstrate "
    "the listed gap skills, building on skills they already have. Return JSON with keys: title, summary, "
    "architecture (list), features (list), phases (list of {name, tasks: list}), tests (list), deployment (list), "
    "repo_structure (string), readme_outline (list), github_setup (list). Be concrete and practical."
)


def llm_plan(
    provider: LLMProvider, gaps: list[str], anchors: list[str], role: str, company: str, jd_summary: str
) -> dict | None:
    base = deterministic_plan(gaps, anchors, role, company)
    prompt = (
        f"Gap skills: {', '.join(display_name(k) for k in gaps)}\n"
        f"Existing skills to build on: {', '.join(display_name(k) for k in anchors)}\n"
        f"Target role: {role} at {company}\n\n" + wrap_untrusted("JOB SUMMARY", jd_summary, 4000)
    )
    try:
        data = provider.complete_json(LLM_SYSTEM, prompt, max_tokens=2500, temperature=0.5)
    except LLMError as exc:
        log.info("project plan LLM failed: %s", exc)
        return None
    plan = dict(base)
    for key in ("title", "summary", "repo_structure"):
        if isinstance(data.get(key), str) and data[key].strip():
            plan[key] = data[key].strip()[:2000]
    for key in ("architecture", "features", "tests", "deployment", "readme_outline", "github_setup"):
        if isinstance(data.get(key), list):
            vals = [str(v)[:300] for v in data[key] if isinstance(v, str | int | float)][:12]
            if vals:
                plan[key] = vals
    if isinstance(data.get("phases"), list):
        phases = []
        for ph in data["phases"][:8]:
            if isinstance(ph, dict) and ph.get("name"):
                tasks = [str(t)[:300] for t in (ph.get("tasks") or []) if isinstance(t, str)][:10]
                phases.append({"name": str(ph["name"])[:120], "tasks": tasks})
        if phases:
            plan["phases"] = phases
    plan["coding_prompt"] = coding_prompt(plan)
    return plan
