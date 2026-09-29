"""Live resume editor: targeted operations, assistant scope, undo/redo, history, conflicts and export."""

import pytest

from app.resume.latex_compile import pdf_text, resolve_engine
from tests.conftest import SAMPLE_JD, run_tasks
from tests.test_api_flow import add_github_project, upload_template

pytestmark = [pytest.mark.latex, pytest.mark.skipif(resolve_engine() is None, reason="no LaTeX engine")]


@pytest.fixture
def generated(auth_client):
    c = auth_client
    tpl = upload_template(c)
    c.post(f"/api/templates/{tpl['id']}/import-profile")
    c.put(
        "/api/profile",
        json={**c.get("/api/profile").json()["personal"], "portfolio_url": "https://candidate.example.dev"},
    )
    add_github_project()
    r = c.post(
        "/api/jobs",
        json={
            "description": SAMPLE_JD,
            "company": "Acme",
            "role_title": "Backend Engineer",
            "resume_instructions": {"controls": {"portfolio": "include"}, "custom": []},
        },
    )
    run_tasks()
    job_id = r.json()["job"]["id"]
    # keep the template's two projects too, so there is something to reorder
    job = c.get(f"/api/jobs/{job_id}").json()
    ids = [p["project_id"] for p in job["recommended_projects"]]
    c.put(f"/api/jobs/{job_id}/projects", json={"project_ids": ids})
    t = c.post(f"/api/jobs/{job_id}/resumes", json={}).json()
    run_tasks()
    vid = c.get(f"/api/tasks/{t['task_id']}").json()["result"]["resume_version_id"]
    return c, vid


def _pdf(c, vid) -> str:
    return pdf_text(c.get(f"/api/resumes/{vid}/pdf").content)


def test_editor_flow(generated):
    c, vid = generated
    s = c.get(f"/api/resumes/{vid}/editor").json()
    assert s["revision"] == 1 and not s["can_undo"]
    assert "Portfolio" in _pdf(c, vid)  # the job instruction included the portfolio link

    # 3-4. manual bullet edit appears in the rendered PDF
    bullet = next(k for k, e in s["elements"].items() if e["type"] == "bullet" and not e["tech_line"])
    r = c.post(
        f"/api/resumes/{vid}/ops",
        json={
            "base_revision": 1,
            "ops": [{"op": "update_text", "id": bullet, "text": "Built REST APIs with FastAPI for request intake."}],
        },
    ).json()
    assert r["revision"] == 2 and "Built REST APIs with FastAPI for request intake." in _pdf(c, vid)
    assert r["elements"][bullet]["origin"] == "user"

    # 5-6. the assistant changes only the summary
    before = r["elements"]
    r = c.post(
        f"/api/resumes/{vid}/assistant",
        json={"instruction": "Make the summary 1 line", "base_revision": 2, "apply": True},
    ).json()
    assert r["applied"] and r["changed"] == ["summary.text"]
    after = r["state"]["elements"]
    assert all(
        after[k]["text"] == v["text"]
        for k, v in before.items()
        if k in after and k != "summary.text" and v["type"] in ("bullet", "paragraph", "item", "skills")
    )

    # 7. move a project
    projects = [k for k, e in after.items() if e["type"] == "entry" and k.startswith("projects.")]
    assert len(projects) >= 2
    last = projects[-1]
    name = after[last]["text"].split(" ")[0]
    r = c.post(
        f"/api/resumes/{vid}/assistant",
        json={"instruction": f"Put {name} first", "base_revision": r["state"]["revision"]},
    ).json()
    assert r["applied"], r
    order = [k for k, e in r["state"]["elements"].items() if e["type"] == "entry" and k.startswith("projects.")]
    assert order[0] == last

    # 8-10. remove a link, undo, redo
    rev = r["state"]["revision"]
    r = c.post(
        f"/api/resumes/{vid}/assistant", json={"instruction": "Remove the portfolio link", "base_revision": rev}
    ).json()
    assert r["applied"] and "Portfolio" not in _pdf(c, vid)
    u = c.post(f"/api/resumes/{vid}/undo", json={"base_revision": r["state"]["revision"]}).json()
    assert "Portfolio" in _pdf(c, vid) and u["can_redo"]
    rd = c.post(f"/api/resumes/{vid}/redo", json={"base_revision": u["revision"]}).json()
    assert "Portfolio" not in _pdf(c, vid)

    # the manual edit survives a broad "make projects concise" request (user edits are preserved)
    r = c.post(
        f"/api/resumes/{vid}/assistant",
        json={"instruction": "Make the projects section more concise", "base_revision": rd["revision"], "apply": True},
    ).json()
    state = r.get("state") or c.get(f"/api/resumes/{vid}/editor").json()
    assert state["elements"][bullet]["text"] == "Built REST APIs with FastAPI for request intake."

    # 11-12. one page, validated layout
    r = c.post(
        f"/api/resumes/{vid}/assistant",
        json={"instruction": "Make the resume exactly one page", "base_revision": state["revision"]},
    ).json()
    assert r["applied"], r
    state = r["state"]
    assert state["page_count"] == 1
    checks = {x["id"]: x["status"] for x in state["quality_report"]["checks"]}
    assert checks["layout"] == "pass" and checks["ats_extraction"] == "pass"

    # 13-16. download the latest PDF and verify it independently
    d = c.get(f"/api/resumes/{vid}/pdf?download=true")
    assert d.status_code == 200 and d.content.startswith(b"%PDF")
    text = pdf_text(d.content)
    assert "Built REST APIs with FastAPI for request intake." in text and "Portfolio" not in text

    # 17-18. a fresh load returns the same state
    again = c.get(f"/api/resumes/{vid}/editor").json()
    assert again["revision"] == state["revision"] and again["content"] == state["content"]

    # 19-20. restore an earlier version; it renders
    r = c.post(f"/api/resumes/{vid}/restore", json={"number": 2, "base_revision": again["revision"]}).json()
    assert r["history"][0]["source"] == "restore" and "Portfolio" in _pdf(c, vid)
    assert len(r["history"]) == again["head"] + 1  # restoring keeps the history in between


def test_stale_edits_are_refused(generated):
    c, vid = generated
    s = c.get(f"/api/resumes/{vid}/editor").json()
    c.post(
        f"/api/resumes/{vid}/ops",
        json={"base_revision": s["revision"], "ops": [{"op": "set_link", "kind": "portfolio", "mode": "exclude"}]},
    )
    stale = c.post(
        f"/api/resumes/{vid}/ops",
        json={"base_revision": s["revision"], "ops": [{"op": "set_link", "kind": "portfolio", "mode": "include"}]},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"


def test_typed_claims_are_flagged_not_trusted(generated):
    c, vid = generated
    s = c.get(f"/api/resumes/{vid}/editor").json()
    bullet = next(k for k, e in s["elements"].items() if e["type"] == "bullet" and not e["tech_line"])
    r = c.post(
        f"/api/resumes/{vid}/ops",
        json={
            "base_revision": s["revision"],
            "ops": [{"op": "update_text", "id": bullet, "text": "Built APIs serving 500,000 users."}],
        },
    ).json()
    assert r["unverified_user_claims"]
    assert {x["id"]: x["status"] for x in r["quality_report"]["checks"]}["user_claims"] == "review"


def test_assistant_refuses_to_add_unevidenced_skills(generated):
    c, vid = generated
    s = c.get(f"/api/resumes/{vid}/editor").json()
    r = c.post(
        f"/api/resumes/{vid}/assistant",
        json={"instruction": "Add Kubernetes to the skills", "base_revision": s["revision"]},
    ).json()
    assert not r["applied"] and any("needs evidence" in s.get("detail", "") for s in r["steps"])


def test_structural_requests_never_call_the_ai_provider(generated):
    from app.llm import provider
    from tests.conftest import FakeLLM

    c, vid = generated
    fake = FakeLLM([])
    provider.set_test_provider(fake)
    rev = c.get(f"/api/resumes/{vid}/editor").json()["revision"]
    for instruction in (
        "Remove the portfolio link",
        "Make the resume exactly one page",
        "Remove the achievements",
        "Keep my portfolio out",
    ):
        r = c.post(
            f"/api/resumes/{vid}/assistant", json={"instruction": instruction, "base_revision": rev, "apply": True}
        )
        assert r.status_code == 200, r.text
        if r.json().get("applied"):
            rev = r.json()["state"]["revision"]
    rev = c.post(f"/api/resumes/{vid}/undo", json={"base_revision": rev}).json()["revision"]
    c.post(f"/api/resumes/{vid}/redo", json={"base_revision": rev})
    assert fake.prompts == []  # deterministic operations: no AI request was made
