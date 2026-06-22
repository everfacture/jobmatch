"""JobMatch LLM HTTP client with automatic provider fallback.

Supports any OpenAI-compatible endpoint (DashScope, OpenAI, local servers).

Environment variables:
  JOBMATCH_LLM_BASE_URL, JOBMATCH_LLM_API_KEY, JOBMATCH_LLM_MODEL
      — canonical primary provider settings
  JOBMATCH_FALLBACK_LLM_BASE_URL, JOBMATCH_FALLBACK_LLM_API_KEY,
  JOBMATCH_FALLBACK_LLM_MODEL
      — canonical fallback provider settings

Legacy aliases are still accepted for migration:
  LLM_URL, LLM_API_KEY, LLM_MODEL
  FALLBACK_LLM_URL, FALLBACK_LLM_API_KEY, FALLBACK_LLM_MODEL
  OPENAI_API_KEY, GEMINI_API_KEY

When a model hits a 429 rate limit or times out after retries, the client
automatically tries the next model in the fallback chain. Exhausted models
get a 5-minute cooldown before being retried.

Qwen optimization: when LLM_MODEL contains 'qwen', /no_think is prepended
to suppress chain-of-thought output (DashScope optimization).
"""

import json
import logging
import os
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelEntry:
    """A model with everything needed to call it."""
    name: str
    label: str           # human-friendly label for logs
    base_url: str
    api_key: str
    extra_body: dict | None = None


def _env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment value from canonical/legacy names."""
    for name in names:
        raw = os.environ.get(name, "")
        if raw.strip():
            return raw.strip()
    return default


def _is_local_url(url: str) -> bool:
    """Return True for local OpenAI-compatible servers that often do not require keys."""
    lowered = url.lower()
    return any(host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


def _json_env(*names: str) -> dict | None:
    """Parse optional JSON object from env, returning None on empty/invalid."""
    raw = _env(*names)
    if not raw:
        return None
    label = "/".join(names)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("Ignoring invalid %s JSON: %s", label, e)
        return None
    if not isinstance(data, dict):
        log.warning("Ignoring %s: expected JSON object", label)
        return None
    return data


def _build_fallback_chain() -> list[ModelEntry]:
    """Build an ordered fallback chain from environment variables."""
    primary_url = _env("JOBMATCH_LLM_BASE_URL", "LLM_URL").rstrip("/")
    primary_key = _env("JOBMATCH_LLM_API_KEY", "LLM_API_KEY")
    primary_model = _env("JOBMATCH_LLM_MODEL", "LLM_MODEL", default="deepseek-chat")
    primary_extra = _json_env("JOBMATCH_LLM_EXTRA_BODY", "LLM_EXTRA_BODY")

    # Backward-compatible convenience: old wizard versions wrote provider-specific
    # API key names without an OpenAI-compatible base URL. Keep those users working
    # while the public config moves to JOBMATCH_LLM_*.
    if not primary_url and os.environ.get("OPENAI_API_KEY"):
        primary_url = "https://api.openai.com/v1"
        primary_key = os.environ.get("OPENAI_API_KEY", "")
        primary_model = _env("JOBMATCH_LLM_MODEL", "LLM_MODEL", default="gpt-4o-mini")
    elif not primary_url and os.environ.get("GEMINI_API_KEY"):
        primary_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        primary_key = os.environ.get("GEMINI_API_KEY", "")
        primary_model = _env("JOBMATCH_LLM_MODEL", "LLM_MODEL", default="gemini-2.0-flash")

    fallback_url = _env("JOBMATCH_FALLBACK_LLM_BASE_URL", "FALLBACK_LLM_URL").rstrip("/")
    fallback_key = _env("JOBMATCH_FALLBACK_LLM_API_KEY", "FALLBACK_LLM_API_KEY")
    fallback_model = _env("JOBMATCH_FALLBACK_LLM_MODEL", "FALLBACK_LLM_MODEL", default="glm-5")
    fallback_extra = _json_env("JOBMATCH_FALLBACK_LLM_EXTRA_BODY", "FALLBACK_LLM_EXTRA_BODY")

    chain: list[ModelEntry] = []

    if primary_url and (primary_key or _is_local_url(primary_url)):
        chain.append(ModelEntry(
            name=primary_model,
            label=f"primary/{primary_model}",
            base_url=primary_url,
            api_key=primary_key,
            extra_body=primary_extra,
        ))

    if fallback_url and (fallback_key or _is_local_url(fallback_url)):
        chain.append(ModelEntry(
            name=fallback_model,
            label=f"fallback/{fallback_model}",
            base_url=fallback_url,
            api_key=fallback_key,
            extra_body=fallback_extra,
        ))

    if not chain:
        raise RuntimeError(
            "No LLM provider configured. "
            "Set JOBMATCH_LLM_BASE_URL and JOBMATCH_LLM_API_KEY "
            "(or legacy LLM_URL and LLM_API_KEY)."
        )

    chain_labels = [e.label for e in chain]
    log.info("LLM fallback chain: %s", " -> ".join(chain_labels))
    return chain


def configured_provider_labels() -> list[str]:
    """Return configured provider labels without making network calls."""
    return [entry.label for entry in _build_fallback_chain()]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_TIMEOUT = 120  # seconds per request
_RATE_LIMIT_BASE_WAIT = 10  # seconds, doubles per retry
_EXHAUSTED_COOLDOWN = 300  # 5 minutes before retrying an exhausted model

# USD per 1M tokens. Best-effort accounting; exact provider billing remains source of truth.
_PRICE_PER_MILLION: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-chat": (0.14, 0.28),  # legacy alias -> DeepSeek V4 Flash non-thinking
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}


def _maybe_prepend_no_think(messages: list[dict]) -> list[dict]:
    """Prepend /no_think to the first user message for Qwen models.

    Qwen models (DashScope) emit chain-of-thought reasoning by default.
    The /no_think prefix suppresses this, saving tokens on structured
    extraction tasks.
    """
    model = _env("JOBMATCH_LLM_MODEL", "LLM_MODEL")
    if "qwen" not in model.lower():
        return messages
    if not messages:
        return messages

    for idx, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if content.startswith("/no_think"):
            return messages
        modified = list(messages)
        modified[idx] = {
            "role": msg["role"],
            "content": f"/no_think\n{content}",
        }
        return modified

    return messages


class LLMClient:
    """Multi-model LLM client with automatic fallback across providers."""

    def __init__(self) -> None:
        self._fallback_chain = _build_fallback_chain()
        self._client = httpx.Client(timeout=_TIMEOUT, trust_env=False)
        self._exhausted: dict[str, float] = {}

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stage: str = "unknown",
    ) -> str:
        """Send a chat completion with automatic fallback.

        Args:
            messages: OpenAI-style message list.
            temperature: Sampling temperature (default: 0.0).
            max_tokens: Maximum output tokens (default: 4096).

        Returns:
            Assistant response text.

        Raises:
            RuntimeError: If all models are exhausted.
        """
        messages = _maybe_prepend_no_think(messages)

        now = time.time()
        entries_to_try = [
            e for e in self._fallback_chain
            if e.name not in self._exhausted
            or (now - self._exhausted[e.name]) > _EXHAUSTED_COOLDOWN
        ]

        if not entries_to_try:
            self._exhausted.clear()
            entries_to_try = list(self._fallback_chain)

        for idx, entry in enumerate(entries_to_try):
            is_last = (idx == len(entries_to_try) - 1)
            result = self._try_entry(entry, messages, temperature, max_tokens, is_last, stage)
            if result is not None:
                if idx > 0:
                    log.info("Used fallback %s (primary was %s)",
                             entry.label, entries_to_try[0].label)
                return result

        raise RuntimeError(
            f"All models exhausted after trying: "
            f"{[e.label for e in entries_to_try]}. "
            f"Wait {_EXHAUSTED_COOLDOWN // 60} minutes for rate limits to reset."
        )

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()

    # -- internal -----------------------------------------------------------

    def _try_entry(
        self,
        entry: ModelEntry,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        is_last: bool,
        stage: str,
    ) -> str | None:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if entry.api_key:
            headers["Authorization"] = f"Bearer {entry.api_key}"

        for attempt in range(_MAX_RETRIES):
            started = time.time()
            try:
                body = {
                    "model": entry.name,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if entry.extra_body:
                    body.update(entry.extra_body)

                resp = self._client.post(
                    f"{entry.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                )

                if resp.status_code in (429, 503):
                    if attempt < _MAX_RETRIES - 1:
                        wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                        log.warning(
                            "%s HTTP %d, retry in %ds (%d/%d)",
                            entry.label, resp.status_code, wait,
                            attempt + 1, _MAX_RETRIES,
                        )
                        time.sleep(wait)
                        continue
                    log.warning("%s exhausted after %d retries, trying next model",
                                entry.label, _MAX_RETRIES)
                    self._exhausted[entry.name] = time.time()
                    return None

                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    body = resp.text[:300]
                    log.error("%s HTTP %d: %s", entry.label, resp.status_code, body)
                    if not is_last:
                        return None
                    resp.raise_for_status()

                if resp.status_code >= 500 and resp.status_code != 503:
                    body = resp.text[:300]
                    log.error("%s HTTP %d: %s", entry.label, resp.status_code, body)
                    if not is_last:
                        return None
                    resp.raise_for_status()

                resp.raise_for_status()
                data = resp.json()
                text = self._extract_text(data, entry)

                if text is not None:
                    self._record_usage(entry, data, stage, time.time() - started)
                    return text

                log.warning("%s returned empty content, trying next model",
                            entry.label)
                if not is_last:
                    return None

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "%s timeout, retry in %ds (%d/%d)",
                        entry.label, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                log.warning("%s timeout after retries, trying next model",
                            entry.label)
                if not is_last:
                    return None
                raise

        return None

    @staticmethod
    def _estimate_cost(model: str, prompt_tokens: int | None,
                       completion_tokens: int | None) -> float | None:
        prices = _PRICE_PER_MILLION.get(model)
        if prices is None or prompt_tokens is None or completion_tokens is None:
            return None
        input_price, output_price = prices
        return (prompt_tokens / 1_000_000 * input_price) + (completion_tokens / 1_000_000 * output_price)

    def _record_usage(self, entry: ModelEntry, data: dict, stage: str, elapsed: float) -> None:
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        estimated_cost = self._estimate_cost(entry.name, prompt_tokens, completion_tokens)
        try:
            from jobmatch.database import record_llm_usage
            record_llm_usage(
                stage=stage,
                provider_label=entry.label,
                model=entry.name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost,
                elapsed_ms=int(elapsed * 1000),
                success=True,
            )
        except Exception as e:
            log.debug("LLM usage logging skipped: %s", e)

    @staticmethod
    def _extract_text(data: dict, entry: ModelEntry) -> str | None:
        choices = data.get("choices")
        if not choices:
            return None
        msg = choices[0].get("message", {})
        text = msg.get("content")
        if text is None:
            return None
        return text


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton."""
    global _instance
    if _instance is None:
        _instance = LLMClient()
    return _instance
