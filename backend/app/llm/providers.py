"""Gemini, OpenRouter and Ollama providers.

Model names change: providers retire models and add new ones. Unless a model is configured explicitly,
each provider picks one from the provider's own current model list, and moves to the next candidate when
the provider reports the model as gone.
"""

import logging
import re
import threading
import time

import httpx

from app.llm.provider import (
    LLMConfig,
    LLMError,
    LLMProvider,
    ModelUnavailable,
    ProviderUnavailable,
    QuotaExceeded,
)

log = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL_LIST_TTL = 6 * 3600

_cache_lock = threading.Lock()
_model_cache: dict[str, tuple[float, list[str]]] = {}


def _cached(key: str, loader) -> list[str]:
    with _cache_lock:
        hit = _model_cache.get(key)
        if hit and time.time() - hit[0] < MODEL_LIST_TTL:
            return hit[1]
    models = loader()
    with _cache_lock:
        _model_cache[key] = (time.time(), models)
    return models


def forget_models(key: str | None = None) -> None:
    with _cache_lock:
        if key is None:
            _model_cache.clear()
        else:
            _model_cache.pop(key, None)


def _retry_after(r: httpx.Response) -> float | None:
    value = r.headers.get("retry-after")
    if value:
        try:
            return float(value)
        except ValueError:
            return None
    # Gemini puts the delay in the error details, e.g. "retryDelay": "37s".
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', r.text or "")
    return float(m.group(1)) if m else None


def _error_message(r: httpx.Response) -> str:
    # Never echo request headers or keys; only the provider's own error message.
    try:
        body = r.json()
        msg = body.get("error", {})
        msg = msg.get("message", "") if isinstance(msg, dict) else str(msg)
    except ValueError:
        msg = ""
    return re.sub(r"(key|token)=[^&\s]+", r"\1=<redacted>", msg)[:300]


def raise_for_status(r: httpx.Response, label: str) -> None:
    if r.status_code == 200:
        return
    msg = _error_message(r)
    if r.status_code == 429:
        raise QuotaExceeded(f"{label} rate limit or quota reached. {msg}".strip(), _retry_after(r))
    if r.status_code in (500, 502, 503, 504, 529):
        raise ProviderUnavailable(f"{label} is temporarily unavailable (HTTP {r.status_code}). {msg}".strip())
    if r.status_code == 404 or (r.status_code == 400 and re.search(r"model|endpoint", msg, re.I)):
        raise ModelUnavailable(f"{label}: {msg or 'model not found'}")
    if r.status_code in (401, 403):
        raise LLMError(f"{label} rejected the API key (HTTP {r.status_code}). {msg}".strip())
    raise LLMError(f"{label} returned HTTP {r.status_code}. {msg}".strip())


def _post(url: str, *, json: dict, headers: dict, timeout: float, label: str) -> httpx.Response:
    try:
        r = httpx.post(url, json=json, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailable(f"{label} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"Could not reach {label} ({type(exc).__name__}).") from exc
    raise_for_status(r, label)
    return r


# --- Gemini -----------------------------------------------------------------------------------------


def gemini_models(api_key: str, timeout: float = 15.0) -> list[str]:
    """Current text models for this key, best candidates first: the Flash alias, then numbered Flash models."""

    def load() -> list[str]:
        r = httpx.get(
            f"{GEMINI_BASE}/models", params={"pageSize": 200}, headers={"x-goog-api-key": api_key}, timeout=timeout
        )
        raise_for_status(r, "Gemini")
        names = [
            m["name"].removeprefix("models/")
            for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        numbered = sorted(
            (n for n in names if re.fullmatch(r"gemini-\d+(\.\d+)?-flash", n)),
            key=lambda n: [int(x) for x in re.findall(r"\d+", n)],
            reverse=True,
        )
        ordered = [n for n in ("gemini-flash-latest",) if n in names] + numbered
        ordered += [n for n in ("gemini-flash-lite-latest",) if n in names]
        return ordered

    return _cached("gemini:" + api_key[-8:], load)


class GeminiProvider(LLMProvider):
    label = "Gemini"

    def candidates(self) -> list[str]:
        if self.cfg.model:
            return [self.cfg.model]
        try:
            found = gemini_models(self.cfg.api_key)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Could not reach Gemini ({type(exc).__name__}).") from exc
        if not found:
            raise ModelUnavailable("Gemini lists no text model for this API key.")
        # The best Flash model, then the lighter model, which is usually available when Flash is overloaded.
        lite = [n for n in found if "lite" in n]
        return list(dict.fromkeys([found[0], *(found[1:2] if not lite else []), *lite[:1]]))

    def _complete(self, system, prompt, max_tokens, temperature):
        last: LLMError | None = None
        for model in self.candidates():
            try:
                return self._call(model, system, prompt, max_tokens, temperature)
            except ModelUnavailable as exc:
                last = exc
                forget_models("gemini:" + self.cfg.api_key[-8:])
            except ProviderUnavailable as exc:  # overloaded model: another model of the same provider may answer
                last = exc
        raise last or ModelUnavailable("No usable Gemini model.")

    def _call(self, model, system, prompt, max_tokens, temperature):
        base = self.cfg.base_url or GEMINI_BASE
        gen = {"temperature": temperature, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
        if re.match(r"gemini-(2\.5)", model):
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        elif model.startswith("gemini-") and "lite" not in model:
            gen["thinkingConfig"] = {"thinkingLevel": "low"}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen,
        }
        headers = {"x-goog-api-key": self.cfg.api_key}
        url = f"{base}/models/{model}:generateContent"
        try:
            r = _post(url, json=body, headers=headers, timeout=self.cfg.timeout, label="Gemini")
        except ModelUnavailable as exc:
            if "thinking" not in str(exc).lower():
                raise
            gen.pop("thinkingConfig", None)
            r = _post(url, json=body, headers=headers, timeout=self.cfg.timeout, label="Gemini")
        data = r.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError) as exc:
            reason = (data.get("candidates") or [{}])[0].get("finishReason", "unknown")
            raise LLMError(f"Gemini returned no content (finish reason: {reason}).") from exc


# --- OpenRouter -------------------------------------------------------------------------------------


def openrouter_free_models(timeout: float = 15.0) -> list[str]:
    """Free text models OpenRouter currently lists, the free-model router first when it exists."""

    def load() -> list[str]:
        r = httpx.get(f"{OPENROUTER_BASE}/models", timeout=timeout)
        raise_for_status(r, "OpenRouter")
        free = []
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            price = m.get("pricing") or {}
            out = (m.get("architecture") or {}).get("output_modalities") or ["text"]
            if not (mid.endswith(":free") or mid == "openrouter/free"):
                continue
            if str(price.get("prompt")) not in ("0", "0.0") or "text" not in out:
                continue
            if "safety" in mid:
                continue
            json_ok = "response_format" in (m.get("supported_parameters") or [])
            free.append((mid != "openrouter/free", not json_ok, -(m.get("context_length") or 0), mid))
        return [f[-1] for f in sorted(free)]

    return _cached("openrouter:free", load)


class OpenRouterProvider(LLMProvider):
    label = "OpenRouter"

    def candidates(self) -> list[str]:
        if self.cfg.model:
            return [self.cfg.model]
        try:
            found = openrouter_free_models()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Could not reach OpenRouter ({type(exc).__name__}).") from exc
        if not found:
            raise ModelUnavailable("OpenRouter lists no free model right now.")
        return found[:3]

    def _complete(self, system, prompt, max_tokens, temperature):
        last: LLMError | None = None
        for model in self.candidates():
            try:
                return self._call(model, system, prompt, max_tokens, temperature)
            except ModelUnavailable as exc:
                last = exc
                forget_models("openrouter:free")
        raise last or ModelUnavailable("No usable OpenRouter model.")

    def _call(self, model, system, prompt, max_tokens, temperature):
        base = (self.cfg.base_url or OPENROUTER_BASE).rstrip("/")
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "X-Title": "CareerPilot"}
        url = f"{base}/chat/completions"
        try:
            r = _post(url, json=body, headers=headers, timeout=self.cfg.timeout, label="OpenRouter")
        except ModelUnavailable as exc:
            if "response_format" not in str(exc) and "json" not in str(exc).lower():
                raise
            body.pop("response_format")
            r = _post(url, json=body, headers=headers, timeout=self.cfg.timeout, label="OpenRouter")
        data = r.json()
        if "error" in data:  # OpenRouter can report upstream errors inside a 200 response
            err = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
            if err.get("code") == 429:
                raise QuotaExceeded(f"OpenRouter rate limit or quota reached. {err.get('message', '')}".strip())
            raise ProviderUnavailable(f"OpenRouter: {str(err.get('message', 'upstream error'))[:200]}")
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("OpenRouter returned an unexpected response.") from exc
        if not content.strip():
            raise ModelUnavailable(f"OpenRouter model {model} returned no text.")
        return content


def openrouter_key_status(api_key: str, timeout: float = 10.0) -> dict | None:
    """Usage figures OpenRouter itself reports for the key (not estimates)."""
    try:
        r = httpx.get(f"{OPENROUTER_BASE}/key", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    d = r.json().get("data") or {}
    free = d.get("free_model_daily_requests") or {}
    return {
        "is_free_tier": d.get("is_free_tier"),
        "credit_limit": d.get("limit"),
        "credit_used": d.get("usage"),
        "free_requests_today": free.get("used"),
        "free_requests_daily_limit": free.get("limit"),
        "free_requests_remaining": free.get("remaining"),
    }


# --- Ollama -----------------------------------------------------------------------------------------


def ollama_models(base_url: str, timeout: float = 3.0) -> list[str]:
    try:
        r = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"Ollama is not running at {base_url}.") from exc
    raise_for_status(r, "Ollama")
    return [m["name"] for m in r.json().get("models", []) if m.get("name")]


class OllamaProvider(LLMProvider):
    label = "Local Ollama"
    external = False

    def _complete(self, system, prompt, max_tokens, temperature):
        base = self.cfg.base_url.rstrip("/")
        model = self.cfg.model
        if not model:
            installed = ollama_models(base)
            if not installed:
                raise ModelUnavailable("Ollama is running but has no model installed (run: ollama pull <model>).")
            model = installed[0]
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "format": "json",
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        r = _post(f"{base}/api/chat", json=body, headers={}, timeout=max(self.cfg.timeout, 180), label="Ollama")
        try:
            return r.json()["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError("Ollama returned an unexpected response.") from exc


def build(cfg: LLMConfig) -> LLMProvider:
    return {"gemini": GeminiProvider, "openrouter": OpenRouterProvider, "ollama": OllamaProvider}[cfg.provider](cfg)
