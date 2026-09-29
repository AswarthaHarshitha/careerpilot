"""Test fixtures.

Every test gets a fresh SQLite database. Background tasks do not run in threads during tests; call
``run_tasks()`` to process queued work synchronously. All data here is synthetic test data.
"""

import os
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

os.environ.update(
    {
        "APP_ENV": "test",
        "WORKER_ENABLED": "false",
        "SECRET_KEY": "test-secret-0123456789-0123456789-0123456789",
        "GEMINI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "OLLAMA_BASE_URL": "",
        "GITHUB_TOKEN": "",
        "ALLOW_REGISTRATION": "false",
        "COOKIE_SECURE": "false",
    }
)

FIXTURES = Path(__file__).parent / "fixtures"
STRONG_PASSWORD = "Correct-Horse-42-battery"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from app.core import config, db, ratelimit
    from app.llm import provider

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    config.get_settings.cache_clear()
    db.reset_engine()
    from app import models  # noqa: F401

    db.Base.metadata.create_all(db.get_engine())
    ratelimit.reset()
    provider.set_test_provider(None)
    yield
    provider.set_test_provider(None)
    db.reset_engine()
    config.get_settings.cache_clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def register(client, email="owner@example.com", password=STRONG_PASSWORD, name="Test Owner"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "display_name": name})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()


@pytest.fixture
def auth_client(client):
    register(client)
    return client


@pytest.fixture
def second_client(monkeypatch):
    """A different user with their own session, for authorization (IDOR) tests."""
    from fastapi.testclient import TestClient

    from app.core import config
    from app.main import create_app

    monkeypatch.setenv("ALLOW_REGISTRATION", "true")
    config.get_settings.cache_clear()
    with TestClient(create_app()) as c:
        register(c, email="other@example.com", name="Other Person")
        yield c
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    config.get_settings.cache_clear()


def run_tasks():
    from app.tasks.queue import run_pending_tasks

    return run_pending_tasks()


class FakeLLM:
    """Scripted provider that goes through the real LLMProvider.complete_json (system prompt, JSON parsing)."""

    def __new__(cls, responses):
        import json

        from app.llm.provider import LLMConfig, LLMProvider

        class _Fake(LLMProvider):
            def __init__(self):
                super().__init__(LLMConfig("fake", "test", ""))
                self.responses = list(responses)
                self.prompts = []

            def _complete(self, system, prompt, max_tokens, temperature):
                self.prompts.append((system, prompt))
                if not self.responses:
                    return "{}"
                r = self.responses.pop(0)
                return json.dumps(r(system, prompt) if callable(r) else r)

        return _Fake()


SAMPLE_JD = """## About the role
You will build backend services for an internal developer platform. Job ID: REQ-20431
Location: Bengaluru, India (Hybrid)

## What you will do
- Design and build REST APIs using Python and FastAPI
- Model data in PostgreSQL and write efficient SQL
- Write automated tests with pytest and review code in Git

## Required qualifications
- Bachelor's degree in Computer Science or a related field
- 0-2 years of experience in software development
- Strong knowledge of Python, SQL and data structures and algorithms
- Experience with Docker

## Preferred qualifications
- Experience with AWS or another cloud platform
- Familiarity with Kubernetes and React

## About us
We build developer tools. Equal opportunity employer.

Apply by 15 October 2026. This is a full-time role.
"""
