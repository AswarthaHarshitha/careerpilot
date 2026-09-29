"""Company research (facts cite fetched pages) and interview preparation (labelled as suggestions)."""

import pytest

from tests.conftest import SAMPLE_JD, run_tasks
from tests.support.mock_site import MockSite


@pytest.fixture
def site(monkeypatch):
    from app.core import config

    with MockSite() as s:
        monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
        config.get_settings.cache_clear()
        yield s
    config.get_settings.cache_clear()


def _job(c, site):
    r = c.post("/api/jobs", json={"url": site.base + "/careers/ibm/jobs/12345"})
    run_tasks()
    return r.json()["job"]["id"]


def test_research_cites_the_pages_it_read(auth_client, site):
    c = auth_client
    job_id = _job(c, site)
    assert c.post(f"/api/jobs/{job_id}/research", json={}).status_code == 200
    run_tasks()
    res = c.get(f"/api/jobs/{job_id}").json()["research"]
    urls = {s["url"] for s in res["sources"]}
    assert site.base + "/" in urls and site.base + "/about" in urls and site.base + "/engineering/blog" in urls
    assert res["overview"][0]["statement"].startswith("We build internal developer platforms")
    assert all(x["source"] in urls for key in ("overview", "technology_signals") for x in res[key])
    techs = {x["statement"].split(" is ")[0] for x in res["technology_signals"]}
    assert {"Kubernetes", "PostgreSQL", "Python"} <= techs


def test_model_research_facts_without_a_real_source_are_dropped(auth_client, site):
    from app.llm import provider
    from tests.conftest import FakeLLM

    c = auth_client
    job_id = _job(c, site)

    def answer(system, prompt):
        assert "Ignore previous instructions" in prompt and "<<UNTRUSTED-" in prompt  # page text fenced as data
        return {
            "overview": [
                {"statement": "Builds developer platforms.", "source": site.base + "/"},
                {"statement": "Raised $2B last week.", "source": "https://news.example.com/x"},
            ],
            "recent_work": [
                {"statement": "Moved the job queue to PostgreSQL.", "source": site.base + "/engineering/blog"}
            ],
            "engineering_areas": [],
            "role_context": ["Platform-facing backend role."],
            "interview_themes": ["Queues and PostgreSQL"],
            "preparation": ["Review SKIP LOCKED."],
        }

    provider.set_test_provider(FakeLLM([answer]))
    c.post(f"/api/jobs/{job_id}/research", json={})
    run_tasks()
    res = c.get(f"/api/jobs/{job_id}").json()["research"]
    assert [x["statement"] for x in res["overview"]] == ["Builds developer platforms."]  # uncited claim dropped
    assert res["recent_work"][0]["source"].endswith("/engineering/blog")
    assert res["interview_themes"] == ["Queues and PostgreSQL"]


def test_ats_only_jobs_explain_what_is_missing(auth_client):
    c = auth_client
    r = c.post("/api/jobs", json={"description": SAMPLE_JD, "company": "Acme", "role_title": "Backend Engineer"})
    run_tasks()
    job_id = r.json()["job"]["id"]
    c.post(f"/api/jobs/{job_id}/research", json={})
    run_tasks()
    res = c.get(f"/api/jobs/{job_id}").json()["research"]
    assert res["sources"] == [] and "company's own website is unknown" in res["limitations"][0]


def test_interview_prep_is_labelled_and_prepared_after_submission(auth_client):
    c = auth_client
    r = c.post("/api/jobs", json={"description": SAMPLE_JD, "company": "Acme", "role_title": "Backend Engineer"})
    run_tasks()
    job = c.get(f"/api/jobs/{r.json()['job']['id']}").json()
    assert job["interview_prep"] is None
    c.patch(f"/api/applications/{job['application']['id']}", json={"status": "submitted"})
    run_tasks()
    prep = c.get(f"/api/jobs/{job['id']}").json()["interview_prep"]
    assert "not known interview questions" in prep["label"]
    assert any("Python" in t for t in prep["technical_areas"])
    assert prep["behavioral"] and prep["coding_topics"]
    assert any("Docker" in t and "gap" in t for t in prep["technical_areas"])
