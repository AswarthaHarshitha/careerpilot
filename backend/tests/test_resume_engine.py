import io

import pytest
from docx import Document

from app.resume.docx_template import parse_docx_template, render_docx
from app.resume.latex_compile import check_template_safety, compile_latex, pdf_text, resolve_engine
from app.resume.latex_render import render_latex
from app.resume.latex_syntax import escape
from app.resume.latex_template import parse_latex_template
from app.resume.review import ats_check, recruiter_check
from app.resume.tailor import JobFocus, ProjectInput, TailorInputs, tailor, to_render_instructions
from app.resume.truth import check_rewrite
from tests.conftest import FIXTURES, FakeLLM

TEX = (FIXTURES / "templates" / "jake_style.tex").read_text()


def test_parser_finds_sections_and_entries():
    s = parse_latex_template(TEX)
    kinds = [(x["kind"], x["layout"]) for x in s["sections"]]
    assert kinds == [
        ("summary", "paragraph"),
        ("experience", "entries"),
        ("projects", "entries"),
        ("skills", "skills"),
        ("education", "entries"),
        ("achievements", "items"),
    ]
    exp = s["sections"][1]["entries"][0]["fields"]
    assert exp == {
        "dates": "Jan 2026 \u2013 Mar 2026",
        "primary": "Example Labs",
        "secondary": "Software Engineering Intern",
        "location": "Remote",
        "short_name": "Example Labs",
    }
    proj = s["sections"][2]["entries"][0]["fields"]
    assert proj["url"] == "https://github.com/example/taskboard" and proj["short_name"] == "Taskboard"
    assert s["header"]["email"] == "candidate@example.com"


def test_render_changes_only_content():
    s = parse_latex_template(TEX)
    out = render_latex(TEX, s, [{"index": 3, "categories": [{"name": "Languages", "items": ["SQL", "Python"]}]}])
    diff = [ln for ln in out.splitlines() if ln not in TEX.splitlines()]
    assert diff == ["     \\textbf{Languages}{: SQL, Python} \\\\"]
    # preamble untouched
    assert out.split("\\begin{document}")[0] == TEX.split("\\begin{document}")[0]


def test_new_entries_are_cloned_from_the_template_shape():
    s = parse_latex_template(TEX)
    out = render_latex(
        TEX,
        s,
        [
            {
                "index": 2,
                "entries": [
                    {
                        "source_index": None,
                        "fields": {"primary": "Resolve", "url": "https://github.com/x/resolve"},
                        "bullets": ["Built issue routing & SLA tracking with FastAPI."],
                    }
                ],
            }
        ],
    )
    assert (
        "\\resumeProjectHeading\n          {\\textbf{Resolve} \\href{https://github.com/x/resolve}{\\faGithub}}{}"
        in out
    )
    assert "\\resumeItem{Built issue routing \\& SLA tracking with FastAPI.}" in out
    assert "Taskboard" not in out.split("\\section{Technical Skills}")[0]


def test_generated_text_cannot_inject_latex():
    assert escape("\\input{/etc/passwd} 100% $x$ #1 _a_ {b}") == (
        "\\textbackslash{}input\\{/etc/passwd\\} 100\\% \\$x\\$ \\#1 \\_a\\_ \\{b\\}"
    )


def test_unsafe_templates_are_rejected():
    assert check_template_safety(TEX) == []
    assert check_template_safety(TEX.replace("\\begin{document}", "\\immediate\\write18{ls}\\begin{document}"))
    assert check_template_safety(TEX.replace("\\begin{document}", "\\input{/etc/passwd}\\begin{document}"))
    assert check_template_safety(TEX.replace("\\begin{document}", "\\input{../secret}\\begin{document}"))


@pytest.mark.latex
@pytest.mark.skipif(resolve_engine() is None, reason="no LaTeX engine installed")
def test_template_compiles_to_one_page():
    res = compile_latex(TEX)
    assert res.error == "" and res.page_count == 1
    assert "Example Labs" in pdf_text(res.pdf)


def test_truth_guard_rejects_invented_claims():
    src = "Built REST APIs with FastAPI and PostgreSQL for request tracking."
    allowed = {"fastapi", "postgresql", "rest_api"}
    assert check_rewrite("Engineered FastAPI REST APIs backed by PostgreSQL for tracking requests.", src, allowed).ok
    assert not check_rewrite("Built FastAPI APIs serving 10,000 users.", src, allowed).ok
    assert not check_rewrite("Built FastAPI APIs deployed on Kubernetes.", src, allowed).ok
    assert not check_rewrite("Led a team building FastAPI APIs.", src, allowed).ok
    assert not check_rewrite("Built FastAPI APIs, improving latency by 40%.", src, allowed).ok


def _inputs():
    s = parse_latex_template(TEX)
    return TailorInputs(
        structure=s,
        focus=JobFocus(
            {"fastapi": 3, "postgresql": 3, "python": 3, "docker": 1.5},
            ["fastapi", "postgresql", "python", "docker"],
            "Backend Engineer",
            "Acme",
        ),
        projects=[
            ProjectInput(
                id=1,
                title="Resolve",
                url="https://github.com/x/resolve",
                description="Issue-resolution platform with SLA tracking",
                bullets=[],
                claimable={"fastapi", "postgresql", "docker"},
                tech=["FastAPI", "PostgreSQL", "Docker"],
                features=["Role-based access for three roles"],
                matched=["FastAPI", "PostgreSQL"],
            )
        ],
        experiences=[],
        profile_skills=["python"],
        strong_skills={"fastapi", "postgresql", "docker"},
        profile_summary="",
    )


def test_tailor_selects_projects_and_adds_only_evidenced_skills():
    content, changes = tailor(_inputs())
    projects = next(s for s in content["sections"] if s["kind"] == "projects")
    assert [e["fields"]["primary"] for e in projects["entries"]] == ["Resolve"]
    assert projects["entries"][0]["bullets"][0]["origin"] == "evidence"
    skills = next(s for s in content["sections"] if s["kind"] == "skills")
    all_items = [i for c in skills["categories"] for i in c["items"]]
    assert "Kubernetes" not in all_items
    assert any(c["type"] == "removed" for c in changes)  # template projects not selected are reported
    instructions = to_render_instructions(content)
    assert render_latex(TEX, parse_latex_template(TEX), instructions)


def test_llm_rewrites_are_guarded():
    def respond(system, prompt):
        import json
        import re

        items = json.loads(re.search(r"\{.*\}", prompt.split(">>\n", 1)[1], re.S).group())
        out = {}
        for k, v in items.items():
            out[k] = v["text"] + " serving 5 million users" if v["kind"] == "bullet" else v["text"]
        return {"items": out}

    content, changes = tailor(_inputs(), FakeLLM([respond]))
    for sec in content["sections"]:
        for e in sec.get("entries") or []:
            for b in e.get("bullets") or []:
                assert "million" not in b["text"]
    assert any(c["type"] == "kept" for c in changes)


def test_ats_and_recruiter_reports_use_labels_not_scores():
    content, _ = tailor(_inputs())
    reqs = [
        {"id": 1, "kind": "skill", "label": "FastAPI", "canonical": "fastapi", "importance": "required"},
        {"id": 2, "kind": "skill", "label": "Kubernetes", "canonical": "kubernetes", "importance": "required"},
    ]
    ats = ats_check(
        "Test Candidate candidate@example.com FastAPI PostgreSQL " * 20,
        content,
        reqs,
        {1: "strong", 2: "missing"},
        1,
        1,
        {"name": "Test Candidate", "email": "candidate@example.com"},
    )
    assert ats["summary"]["jd_coverage"] in ("Strong", "Good", "Fair", "Weak")
    assert [m["label"] for m in ats["missing_without_evidence"]] == ["Kubernetes"]
    rec = recruiter_check(content, reqs, {1: "strong", 2: "missing"}, 1, 1)
    assert rec["summary"]["potential_gaps"] == 1
    assert "score" not in str(ats["summary"]).lower()


def test_docx_round_trip_keeps_styles():
    data = (FIXTURES / "templates" / "simple_resume.docx").read_bytes()
    s = parse_docx_template(data)
    assert [x["kind"] for x in s["sections"]] == ["summary", "experience", "projects", "skills", "education"]
    out = render_docx(
        data,
        s,
        [
            {
                "index": 2,
                "entries": [{"source_index": None, "fields": {"primary": "Resolve"}, "bullets": ["Built routing."]}],
            }
        ],
    )
    paras = Document(io.BytesIO(out)).paragraphs
    texts = [(p.text, p.style.name) for p in paras]
    assert ("Built routing.", "List Bullet") in texts
    assert ("Resolve", "Normal") in texts
