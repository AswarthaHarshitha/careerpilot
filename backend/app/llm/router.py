"""Chooses the AI provider for a user and falls back between providers.

Rules:
- The user picks a provider (or "auto", which follows the server's order) and whether fallback is allowed.
- Fallback only moves to providers that are configured and permitted. With privacy mode on, resume and
  profile content goes only to the local provider (Ollama); external providers are never tried.
- Rate limits (HTTP 429) and temporary failures are retried with exponential backoff; a provider that asks
  for a long wait is skipped and cooled down instead of blocking the request.
- Every request is counted per user, provider and day (application-side usage; not the provider's quota).
- Which provider answered is recorded on the chain (``used``) so the UI can say so.
"""

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.llm.provider import (
    LLMConfig,
    LLMError,
    LLMProvider,
    ModelUnavailable,
    ProviderUnavailable,
    QuotaExceeded,
    active_test_override,
)
from app.llm.providers import build

log = logging.getLogger(__name__)

PROVIDERS = ("gemini", "openrouter", "ollama")
LABELS = {"gemini": "Gemini", "openrouter": "OpenRouter", "ollama": "Local Ollama"}
MAX_ATTEMPTS = 3  # per provider
BACKOFF_BASE = 1.0  # seconds: 1, 2, 4 ...
MAX_WAIT = 8.0  # longer requested waits skip to the next provider instead
ALL_UNAVAILABLE = (
    "Free AI providers are temporarily unavailable. You can retry later, enable local Ollama, or configure "
    "another provider in Settings → AI providers. Your work is saved; this step used deterministic processing."
)

_sleep = time.sleep  # replaced in tests
_cool_lock = threading.Lock()
_cooldown: dict[str, float] = {}


@dataclass
class Choice:
    provider: str
    source: str  # "your key" | "server"
    cfg: LLMConfig


def configured(user_settings) -> dict[str, Choice]:
    """Providers that have what they need, keyed by provider name. Personal keys win over server keys."""
    s = get_settings()
    out: dict[str, Choice] = {}
    mine = decrypt_secret(getattr(user_settings, "gemini_key_encrypted", None)) if user_settings else ""
    key = mine or s.gemini_api_key
    if key:
        out["gemini"] = Choice(
            "gemini",
            "your key" if mine else "server",
            LLMConfig("gemini", s.gemini_model, key, timeout=s.llm_timeout_seconds),
        )
    mine = decrypt_secret(getattr(user_settings, "openrouter_key_encrypted", None)) if user_settings else ""
    key = mine or s.openrouter_api_key
    if key:
        out["openrouter"] = Choice(
            "openrouter",
            "your key" if mine else "server",
            LLMConfig("openrouter", s.openrouter_model, key, timeout=s.llm_timeout_seconds),
        )
    if s.ollama_base_url:
        out["ollama"] = Choice(
            "ollama", "server", LLMConfig("ollama", s.ollama_model, "", s.ollama_base_url, s.llm_timeout_seconds)
        )
    return out


def plan(user_settings) -> tuple[list[Choice], str]:
    """Providers to try, in order, plus a note explaining any restriction (shown in Settings)."""
    available = configured(user_settings)
    choice = getattr(user_settings, "ai_provider", "auto") if user_settings is not None else "auto"
    fallback = getattr(user_settings, "ai_fallback_enabled", True) if user_settings is not None else True
    privacy = bool(getattr(user_settings, "ai_privacy_mode", False)) if user_settings is not None else False
    if choice == "none":
        return [], "AI is turned off; every step uses deterministic processing."
    order = [p for p in get_settings().ai_provider_order if p in PROVIDERS] or list(PROVIDERS)
    if choice in PROVIDERS:
        order = [choice] + [p for p in order if p != choice]
    if privacy:
        order = [p for p in order if p == "ollama"]
    names = [p for p in order if p in available]
    if choice in PROVIDERS and choice not in available and not privacy:
        note = f"{LABELS[choice]} is not configured."
    elif privacy and not names:
        return [], "Privacy mode is on and no local model is configured, so AI steps use deterministic processing."
    else:
        note = ""
    if choice in PROVIDERS and choice not in available:
        names = [] if not fallback else names
    if not fallback:
        names = names[:1]
    return [available[n] for n in names], note


def _cool_key(c: Choice) -> str:
    return c.provider + ":" + hashlib.sha256((c.cfg.api_key or c.cfg.base_url).encode()).hexdigest()[:12]


def cooling_until(c: Choice) -> float:
    with _cool_lock:
        return _cooldown.get(_cool_key(c), 0.0)


def _cool(c: Choice, seconds: float) -> None:
    with _cool_lock:
        _cooldown[_cool_key(c)] = time.time() + seconds


def reset_cooldowns() -> None:
    with _cool_lock:
        _cooldown.clear()


class ProviderChain(LLMProvider):
    """Tries each permitted provider in order; behaves like a single provider to callers."""

    def __init__(self, choices: list[Choice], user_id: int | None, *, use_cache: bool = True):
        super().__init__(choices[0].cfg)
        self.choices = choices
        self.user_id = user_id
        self.use_cache = use_cache
        self.label = LABELS[choices[0].provider]
        self.external = any(build(c.cfg).external for c in choices)

    @property
    def name(self) -> str:
        return " → ".join(LABELS[c.provider] for c in self.choices)

    def _complete(self, system, prompt, max_tokens, temperature):
        self.used = ""
        key = cache_key(system, prompt, max_tokens, temperature) if self.use_cache else None
        hit = cache_get(self.user_id, key)
        if hit is not None:
            self.used = hit[0] + " (cached)"
            return hit[1]
        text = self._complete_uncached(system, prompt, max_tokens, temperature)
        cache_put(self.user_id, key, self.used, text)
        return text

    def _complete_uncached(self, system, prompt, max_tokens, temperature):
        errors: list[str] = []
        quota_only = True
        for i, c in enumerate(self.choices):
            wait = cooling_until(c) - time.time()
            if wait > 0:
                self.notices.append(f"{LABELS[c.provider]} is cooling down after a rate limit ({int(wait)}s left).")
                errors.append(f"{LABELS[c.provider]}: cooling down")
                continue
            provider = build(c.cfg)
            for attempt in range(MAX_ATTEMPTS):
                try:
                    text = provider._complete(system, prompt, max_tokens, temperature)
                except QuotaExceeded as exc:
                    record(self.user_id, c.provider, "rate_limited", str(exc))
                    delay = exc.retry_after if exc.retry_after is not None else BACKOFF_BASE * 2**attempt
                    if delay > MAX_WAIT or attempt == MAX_ATTEMPTS - 1:
                        _cool(c, max(delay, 30.0))
                        errors.append(str(exc))
                        break
                    _sleep(delay)
                    continue
                except ProviderUnavailable as exc:
                    record(self.user_id, c.provider, "failed", str(exc))
                    quota_only = False
                    if attempt == MAX_ATTEMPTS - 1:
                        errors.append(str(exc))
                        break
                    _sleep(BACKOFF_BASE * 2**attempt)
                    continue
                except (ModelUnavailable, LLMError) as exc:
                    record(self.user_id, c.provider, "failed", str(exc))
                    quota_only = False
                    errors.append(str(exc))
                    break
                record(self.user_id, c.provider, "ok")
                self.used = LABELS[c.provider]
                return text
            if i + 1 < len(self.choices):
                nxt = LABELS[self.choices[i + 1].provider]
                reason = "quota reached" if "rate limit" in errors[-1] else "unavailable"
                self.notices.append(f"{LABELS[c.provider]} {reason}. Trying {nxt}…")
                log.info("AI provider %s %s; falling back to %s", c.provider, reason, nxt)
        if len(self.choices) > 1:
            raise LLMError(ALL_UNAVAILABLE if quota_only else ALL_UNAVAILABLE + " Details: " + "; ".join(errors))
        if quota_only:
            raise LLMError(
                f"{self.label} quota reached. You can retry later, turn on fallback, or configure another provider. "
                "Your work is saved; this step used deterministic processing."
            )
        raise LLMError(errors[-1] if errors else "The AI provider did not answer.")


def provider_for(user_settings) -> LLMProvider | None:
    override = active_test_override()
    if override is not None:
        return override
    choices, _ = plan(user_settings)
    if not choices:
        return None
    return ProviderChain(choices, getattr(user_settings, "user_id", None))


def single(user_settings, provider: str) -> LLMProvider | None:
    """One specific provider without fallback or cache (used by Test connection, which must reach the provider)."""
    c = configured(user_settings).get(provider)
    if c is None:
        return None
    return ProviderChain([c], getattr(user_settings, "user_id", None), use_cache=False)


# --- response cache ---------------------------------------------------------------------------------

CACHE_DAYS = 30
CACHE_MAX_TEMPERATURE = 0.25  # higher-temperature requests (rewrites) are expected to vary, so never cached


def cache_key(system: str, prompt: str, max_tokens: int, temperature: float) -> str | None:
    if temperature > CACHE_MAX_TEMPERATURE:
        return None
    # Untrusted content is fenced with random markers on every call; they must not defeat the cache.
    text = re.sub(r"UNTRUSTED-[0-9a-f]{12}", "UNTRUSTED", f"{system}\x00{prompt}\x00{max_tokens}\x00{temperature}")
    return hashlib.sha256(text.encode()).hexdigest()


def cache_get(user_id: int | None, key: str | None) -> tuple[str, str] | None:
    if user_id is None or key is None:
        return None
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.db import session_factory
    from app.models import AIResponseCache

    try:
        with session_factory()() as db:
            row = db.scalars(
                select(AIResponseCache).where(AIResponseCache.user_id == user_id, AIResponseCache.key == key)
            ).first()
            if row is None or datetime.now(UTC) - row.created_at > timedelta(days=CACHE_DAYS):
                return None
            return row.provider, row.response
    except Exception:  # noqa: BLE001 - the cache is an optimisation only
        return None


def cache_put(user_id: int | None, key: str | None, provider: str, response: str) -> None:
    if user_id is None or key is None or not provider:
        return
    from sqlalchemy import delete

    from app.core.db import session_factory
    from app.models import AIResponseCache

    try:
        with session_factory()() as db:
            db.execute(delete(AIResponseCache).where(AIResponseCache.user_id == user_id, AIResponseCache.key == key))
            db.add(AIResponseCache(user_id=user_id, key=key, provider=provider, response=response))
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("Could not store an AI response in the cache", exc_info=True)


# --- usage accounting -------------------------------------------------------------------------------


def record(user_id: int | None, provider: str, outcome: str, error: str = "") -> None:
    """Counts one request. Uses its own session so it never interferes with the caller's transaction."""
    if user_id is None:
        return
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.core.db import session_factory
    from app.models import AIUsage

    now = datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    try:
        db = session_factory()()
    except Exception:  # noqa: BLE001 - accounting must never break an AI call
        return
    try:
        for _ in range(2):
            row = db.scalars(
                select(AIUsage).where(AIUsage.user_id == user_id, AIUsage.provider == provider, AIUsage.day == day)
            ).first()
            if row is None:
                row = AIUsage(
                    user_id=user_id,
                    provider=provider,
                    day=day,
                    requests=0,
                    succeeded=0,
                    rate_limited=0,
                    failed=0,
                    last_error="",
                )
                db.add(row)
            row.requests += 1
            row.last_used_at = now
            if outcome == "ok":
                row.succeeded += 1
            elif outcome == "rate_limited":
                row.rate_limited += 1
            else:
                row.failed += 1
            if error:
                row.last_error, row.last_error_at = error[:400], now
            try:
                db.commit()
                return
            except IntegrityError:
                db.rollback()
    except Exception:  # noqa: BLE001
        log.warning("Could not record AI usage", exc_info=True)
        db.rollback()
    finally:
        db.close()


def describe(provider: LLMProvider | None) -> str:
    """One line for task steps and the editor: which provider answered, and any fallback that happened."""
    if provider is None:
        return ""
    parts = list(provider.notices)
    if provider.used:
        parts.append(f"AI provider: {provider.used}")
    return " ".join(parts)
