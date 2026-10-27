"""AI provider layer: model discovery, rate limits and fallback, privacy mode, usage accounting, key handling.

HTTP is stubbed at the provider module, so these tests never reach a real provider.
"""

import json

import httpx
import pytest

from app.llm import providers, router
from app.llm.provider import LLMError


class Stub:
    """Routes httpx.get/post calls by URL substring to canned responses (callables or (status, body))."""

    def __init__(self, monkeypatch):
        self.routes: list[tuple[str, object]] = []
        self.calls: list[str] = []
        monkeypatch.setattr(providers.httpx, "post", self._handle)
        monkeypatch.setattr(providers.httpx, "get", self._handle)

    def on(self, fragment, *responses):
        self.routes.append((fragment, list(responses)))

    def _handle(self, url, **kw):
        self.calls.append(url)
        for fragment, responses in self.routes:
            if fragment in url:
                r = responses.pop(0) if len(responses) > 1 else responses[0]
                if callable(r):
                    r = r(url, kw)
                status, body = r
                headers = {}
                if isinstance(body, tuple):
                    body, headers = body
                return httpx.Response(status, json=body, headers=headers, request=httpx.Request("POST", url))
        raise httpx.ConnectError("no route for " + url)


GEMINI_MODELS = {
    "models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
    ]
}
OPENROUTER_MODELS = {
    "data": [
        {"id": "vendor/paid-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "vendor/small:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 8000},
        {
            "id": "openrouter/free",
            "pricing": {"prompt": "0", "completion": "0"},
            "context_length": 200000,
            "supported_parameters": ["response_format"],
        },
        {"id": "google/lyria:free", "pricing": {"prompt": "0"}, "architecture": {"output_modalities": ["audio"]}},
    ]
}


def gemini_ok(text='{"ok": true}'):
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def openrouter_ok(text='{"ok": true}'):
    return 200, {"choices": [{"message": {"content": text}}]}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    providers.forget_models()
    router.reset_cooldowns()
    monkeypatch.setattr(router, "_sleep", lambda s: None)
    yield
    providers.forget_models()
    router.reset_cooldowns()


def _settings(monkeypatch, **env):
    from app.core import config

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()


def _user_settings(**kw):
    from app.models import UserSettings

    base = {"user_id": None, "ai_provider": "auto", "ai_fallback_enabled": True, "ai_privacy_mode": False}
    return UserSettings(**{**base, **kw})


def test_gemini_discovers_current_model_and_skips_retired_ones(monkeypatch):
    _settings(monkeypatch, GEMINI_API_KEY="g-key-123456789")
    stub = Stub(monkeypatch)
    stub.on("gemini-flash-latest:generateContent", (404, {"error": {"message": "model is no longer available"}}))
    stub.on("gemini-3.5-flash:generateContent", gemini_ok('{"answer": 42}'))
    stub.on("/v1beta/models", (200, GEMINI_MODELS))
    p = router.provider_for(_user_settings())
    assert p.complete_json("sys", "prompt") == {"answer": 42}
    assert p.used == "Gemini"
    tried = [c for c in stub.calls if "generateContent" in c]
    assert "gemini-flash-latest" in tried[0] and "gemini-3.5-flash" in tried[1]
    assert providers.gemini_models("g-key-123456789")[:2] == ["gemini-flash-latest", "gemini-3.5-flash"]


def test_openrouter_uses_a_listed_free_model(monkeypatch):
    _settings(monkeypatch, OPENROUTER_API_KEY="or-key-123456789")
    stub = Stub(monkeypatch)
    stub.on("/api/v1/models", (200, OPENROUTER_MODELS))
    seen = {}

    def chat(url, kw):
        seen["model"] = kw["json"]["model"]
        return openrouter_ok()

    stub.on("/chat/completions", chat)
    assert providers.openrouter_free_models() == ["openrouter/free", "vendor/small:free"]
    p = router.provider_for(_user_settings())
    assert p.complete_json("s", "p") == {"ok": True} and seen["model"] == "openrouter/free"


def test_rate_limit_backs_off_then_falls_back_and_is_counted(monkeypatch, client):
    from tests.conftest import register

    register(client)
    _settings(
        monkeypatch,
        GEMINI_API_KEY="g-key-123456789",
        OPENROUTER_API_KEY="or-key-123456789",
        GEMINI_MODEL="gemini-x",
        OPENROUTER_MODEL="vendor/small:free",
    )
    stub = Stub(monkeypatch)
    sleeps = []
    monkeypatch.setattr(router, "_sleep", sleeps.append)
    stub.on("gemini-x:generateContent", (429, {"error": {"message": "Resource exhausted"}}))
    stub.on("/chat/completions", openrouter_ok('{"from": "openrouter"}'))
    from app.core.db import session_factory
    from app.models import AIUsage, UserSettings

    db = session_factory()()
    s = db.query(UserSettings).one()
    p = router.provider_for(s)
    assert p.complete_json("s", "p") == {"from": "openrouter"}
    assert sleeps == [1.0, 2.0]  # exponential backoff before giving up on Gemini
    assert p.used == "OpenRouter"
    assert any("Gemini quota reached" in n and "Trying OpenRouter" in n for n in p.notices)
    db.close()
    db = session_factory()()
    usage = {u.provider: u for u in db.query(AIUsage).all()}
    assert usage["gemini"].rate_limited == 3 and usage["gemini"].requests == 3
    assert usage["openrouter"].succeeded == 1
    db.close()
    # Gemini is now cooling down: the next request goes straight to OpenRouter.
    p2 = router.provider_for(s)
    p2.complete_json("s", "p")
    assert sum("gemini-x" in c for c in stub.calls) == 3
    body = client.get("/api/settings/ai/usage").json()
    gem = next(x for x in body["providers"] if x["id"] == "gemini")
    assert gem["requests_today"] == 3 and gem["cooling_down_seconds"] > 0 and "Resource exhausted" in gem["last_error"]


def test_long_retry_after_skips_waiting(monkeypatch):
    _settings(
        monkeypatch,
        GEMINI_API_KEY="g-key-123456789",
        OPENROUTER_API_KEY="or-key-123456789",
        GEMINI_MODEL="gemini-x",
        OPENROUTER_MODEL="m:free",
    )
    stub = Stub(monkeypatch)
    sleeps = []
    monkeypatch.setattr(router, "_sleep", sleeps.append)
    stub.on("generateContent", (429, ({"error": {"message": "quota"}}, {"retry-after": "45"})))
    stub.on("/chat/completions", openrouter_ok())
    router.provider_for(_user_settings()).complete_json("s", "p")
    assert sleeps == []


def test_all_providers_exhausted_gives_clear_message(monkeypatch):
    _settings(
        monkeypatch,
        GEMINI_API_KEY="g-key-123456789",
        OPENROUTER_API_KEY="or-key-123456789",
        GEMINI_MODEL="gemini-x",
        OPENROUTER_MODEL="m:free",
    )
    stub = Stub(monkeypatch)
    stub.on("generateContent", (429, {"error": {"message": "quota"}}))
    stub.on("/chat/completions", (429, {"error": {"message": "free-models-per-day"}}))
    with pytest.raises(LLMError) as e:
        router.provider_for(_user_settings()).complete_json("s", "p")
    assert "Free AI providers are temporarily unavailable" in str(e.value) and "Ollama" in str(e.value)


def test_fallback_off_uses_only_the_chosen_provider(monkeypatch):
    _settings(
        monkeypatch, GEMINI_API_KEY="g-key-123456789", OPENROUTER_API_KEY="or-key-123456789", GEMINI_MODEL="gemini-x"
    )
    stub = Stub(monkeypatch)
    stub.on("generateContent", (429, {"error": {"message": "quota"}}))
    stub.on("/chat/completions", openrouter_ok())
    p = router.provider_for(_user_settings(ai_fallback_enabled=False))
    with pytest.raises(LLMError, match="Gemini quota reached"):
        p.complete_json("s", "p")
    assert not any("chat/completions" in c for c in stub.calls)


def test_privacy_mode_never_uses_external_providers(monkeypatch):
    _settings(monkeypatch, GEMINI_API_KEY="g-key-123456789", OPENROUTER_API_KEY="or-key-123456789")
    assert router.provider_for(_user_settings(ai_privacy_mode=True)) is None
    _, note = router.plan(_user_settings(ai_privacy_mode=True))
    assert "Privacy mode" in note
    _settings(monkeypatch, OLLAMA_BASE_URL="http://127.0.0.1:11434", OLLAMA_MODEL="llama-local")
    stub = Stub(monkeypatch)
    stub.on("/api/chat", (200, {"message": {"content": '{"local": true}'}}))
    p = router.provider_for(_user_settings(ai_privacy_mode=True))
    assert p.complete_json("s", "p") == {"local": True} and p.used == "Local Ollama" and not p.external
    assert all("11434" in c for c in stub.calls)


def test_ollama_down_is_reported_not_crashing(monkeypatch):
    _settings(monkeypatch, OLLAMA_BASE_URL="http://127.0.0.1:9")
    Stub(monkeypatch)
    with pytest.raises(LLMError):
        router.provider_for(_user_settings(ai_provider="ollama")).complete_json("s", "p")


def test_chosen_provider_goes_first(monkeypatch):
    _settings(monkeypatch, GEMINI_API_KEY="g-key-123456789", OPENROUTER_API_KEY="or-key-123456789")
    chain, _ = router.plan(_user_settings(ai_provider="openrouter"))
    assert [c.provider for c in chain] == ["openrouter", "gemini"]
    assert router.provider_for(_user_settings(ai_provider="none")) is None


def test_settings_page_masks_keys_and_tests_connection(monkeypatch, auth_client):
    _settings(monkeypatch, GEMINI_MODEL="gemini-x")
    stub = Stub(monkeypatch)
    stub.on("generateContent", gemini_ok())
    key = "AIzaSyPERSONAL-abcdefghijklmnopqrstuv"
    s = auth_client.put("/api/settings/ai", json={"provider": "gemini", "gemini_key": key}).json()
    assert key not in json.dumps(s)
    r = auth_client.post("/api/settings/ai/test", json={"provider": "gemini"})
    assert r.status_code == 200 and r.json()["ok"] is True
    auth_client.post("/api/settings/ai/test", json={"provider": "gemini"})
    assert sum("generateContent" in c for c in stub.calls) == 2  # a connection test is never answered from cache
    r = auth_client.post("/api/settings/ai/test", json={"provider": "openrouter"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_configured"
    usage = auth_client.get("/api/settings/ai/usage").json()
    assert usage["current_provider"] == "Gemini"
    assert key not in auth_client.get("/api/data/export").text


def test_health_does_not_expose_keys(monkeypatch, client):
    _settings(monkeypatch, GEMINI_API_KEY="g-secret-key-987654321")
    body = client.get("/api/health").json()
    assert body["ai_providers"] == ["gemini"] and "g-secret" not in json.dumps(body)


def test_onboarding_flag(auth_client):
    assert auth_client.get("/api/settings").json()["onboarded"] is False
    assert auth_client.post("/api/settings/onboarding/complete").json()["onboarded"] is True


def test_identical_requests_are_answered_from_the_cache(monkeypatch, client):
    from app.core.db import session_factory
    from app.llm.provider import wrap_untrusted
    from app.models import UserSettings
    from tests.conftest import register

    register(client)
    _settings(monkeypatch, GEMINI_API_KEY="g-key-123456789", GEMINI_MODEL="gemini-x")
    stub = Stub(monkeypatch)
    stub.on("generateContent", gemini_ok('{"skills": ["python"]}'))
    with session_factory()() as db:
        s = db.query(UserSettings).one()
        for _ in range(2):
            p = router.provider_for(s)
            assert p.complete_json("extract", wrap_untrusted("JOB", "Python developer")) == {"skills": ["python"]}
        assert p.used == "Gemini (cached)"
        assert sum("generateContent" in c for c in stub.calls) == 1
        # rewrites (higher temperature) always go to the provider
        router.provider_for(s).complete_json("rewrite", "text", temperature=0.4)
        router.provider_for(s).complete_json("rewrite", "text", temperature=0.4)
        assert sum("generateContent" in c for c in stub.calls) == 3


def test_gemini_used_up_flash_quota_moves_to_lite_and_is_remembered(monkeypatch):
    from app.llm.provider import LLMConfig, QuotaExceeded

    monkeypatch.setattr(providers, "_quota_until", {})
    monkeypatch.setattr(providers, "gemini_models", lambda key: ["gemini-flash-latest", "gemini-flash-lite-latest"])
    calls = []

    def call(self, model, *a):
        calls.append(model)
        if "lite" not in model:
            raise QuotaExceeded("Gemini rate limit or quota reached.", 30)
        return '{"ok": true}'

    monkeypatch.setattr(providers.GeminiProvider, "_call", call)
    p = providers.GeminiProvider(LLMConfig("gemini", "", "key-12345678"))
    assert p._complete("s", "p", 100, 0.1) == '{"ok": true}'
    assert p._complete("s", "p", 100, 0.1) == '{"ok": true}'
    assert calls == ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-flash-lite-latest"]


def test_background_work_prefers_lighter_gemini_models(monkeypatch):
    from app.llm.provider import LLMConfig

    monkeypatch.setattr(providers, "gemini_models", lambda key: ["gemini-flash-latest", "gemini-flash-lite-latest"])
    p = providers.GeminiProvider(LLMConfig("gemini", "", "key-12345678"))
    assert p.candidates()[0] == "gemini-flash-latest"
    token = providers.prefer_light.set(True)
    try:
        assert p.candidates()[0] == "gemini-flash-lite-latest"
    finally:
        providers.prefer_light.reset(token)
