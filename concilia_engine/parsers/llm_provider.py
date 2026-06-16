"""LLM Provider Protocol and LiteLLM implementation with retry and rate-limit handling.

Architecture:
  1. LLMProvider (Protocol) — contract for any LLM backend.
  2. LiteLLMProvider — concrete implementation using LiteLLM gateway.
  3. _resolve_api_key — maps model prefix to environment variable chain.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Protocol

from concilia_engine.config import LLMConfig

logger = logging.getLogger(__name__)

# Model prefix → env var chain for API key resolution.
# First match wins; falls through to LLM_API_KEY for unknown prefixes.
PREFIX_KEY_MAP: dict[str, list[str]] = {
    "gemini/":       ["GEMINI_API_KEY", "GOOGLE_API_KEY", "LLM_API_KEY"],
    "nvidia_nim/":   ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "LLM_API_KEY"],
    "huggingface/":  ["HF_API_KEY", "HUGGINGFACE_API_KEY", "LLM_SECOND_BACKUP_KEY", "LLM_API_KEY"],
    "openai/":       ["OPENAI_API_KEY", "LLM_API_KEY"],
    "anthropic/":    ["ANTHROPIC_API_KEY", "LLM_API_KEY"],
    "deepseek/":     ["DEEPSEEK_API_KEY", "LLM_API_KEY"],
}

# Error types that are worth retrying (transient / network issues).
RETRYABLE_EXCEPTIONS = (ConnectionError, TimeoutError, OSError)


# Model-specific limits for max_tokens and max_context_chars.
# Keys match model identifiers; use substring matching (first match wins).
MODEL_LIMITS: dict[str, dict[str, int]] = {
    # VL model: 16K total context (input+output), max 2048 output tokens
    "llama-3.1-nemotron-nano-vl": {"max_tokens": 2048, "max_context_chars": 12000},
    # Nemotron reasoning model
    "nemotron-3-nano-30b": {"max_tokens": 2048, "max_context_chars": 40000},
}


def _model_limits(model: str, default_tokens: int, default_chars: int) -> tuple[int, int]:
    """Return (max_tokens, max_context_chars) for the given model.

    Falls back to *default_tokens* / *default_chars* for unknown models.
    """
    for key, limits in MODEL_LIMITS.items():
        if key in model:
            return (limits["max_tokens"], limits["max_context_chars"])
    return (default_tokens, default_chars)


def _resolve_api_key(model: str) -> str | None:
    """Resolve the API key for a model based on its provider prefix.

    Searches a chain of environment variables defined in :data:`PREFIX_KEY_MAP`.
    Returns ``None`` only if no key is found for a known prefix.
    """
    for prefix, env_vars in PREFIX_KEY_MAP.items():
        if model.startswith(prefix):
            for var in env_vars:
                key = os.getenv(var)
                if key:
                    return key
            return None
    # Unknown prefix — fall back to generic LLM_API_KEY
    return os.getenv("LLM_API_KEY")


def _is_rate_limit(error: Exception) -> bool:
    """Return True if the error is a rate-limit / quota exhaustion (HTTP 429)."""
    msg = str(error).lower()
    return any(kw in msg for kw in ("429", "rate limit", "quota", "exceeded your current quota"))


def _is_retryable(error: Exception) -> bool:
    """Return True if the error is transient and worth retrying."""
    return isinstance(error, RETRYABLE_EXCEPTIONS)


class LLMProvider(Protocol):
    """Contract for an LLM backend used by the parsing engine."""

    def generate(self, prompt: str, model: str, api_key: str, config: LLMConfig) -> str | None: ...
    def translate_error(self, error: Exception) -> str: ...


class LiteLLMProvider:
    """LLM provider backed by the LiteLLM gateway with automatic retry.

    Retry strategy
    ---------------
    * **Rate limits (429)** — exponential backoff 6s → 12s → 24s, up to 3 attempts.
      Rate limits do **not** count as permanent failures; they are transient.
    * **Connection / timeout** — exponential backoff 2s → 4s → 8s, up to 3 attempts.
    * **Everything else** — logged and returned immediately as failure.
    """

    def _call_completion(
        self, model: str, api_key: str, prompt: str, config: LLMConfig
    ) -> str:
        """Single LiteLLM call (no retry — callers manage that)."""
        from litellm import completion

        max_tokens, _ = _model_limits(model, config.max_tokens, config.max_context_chars)

        response = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key,
            max_tokens=max_tokens,
            timeout=config.timeout,
        )
        return response.choices[0].message.content or ""

    def generate(
        self, prompt: str, model: str, api_key: str, config: LLMConfig
    ) -> str | None:
        """Attempt to generate a response with up to 3 retries.

        Returns the raw text response, or ``None`` if all attempts fail.
        """
        for attempt in range(1, 4):
            try:
                return self._call_completion(model, api_key, prompt, config)
            except Exception as e:
                if _is_rate_limit(e):
                    wait = min(2 ** attempt * 3, 60)
                    logger.warning(
                        "Rate limit on %s (attempt %d/3, %ds backoff)",
                        model, attempt, wait,
                    )
                    time.sleep(wait)
                    continue

                if _is_retryable(e):
                    wait = min(2 ** attempt, 30)
                    logger.warning(
                        "Transient error on %s (attempt %d/3, %ds backoff): %s",
                        model, attempt, wait, str(e)[:100],
                    )
                    time.sleep(wait)
                    continue

                logger.warning("LLM call to %s failed: %s", model, str(e)[:200])
                return None

        logger.warning("LLM call to %s exhausted all retries (3)", model)
        return None

    def translate_error(self, error: Exception) -> str:
        """Return a user-friendly Spanish error message for a provider error."""
        msg = str(error).lower()
        if "429" in msg or "rate limit" in msg or "quota" in msg:
            return "Limite de cuota excedido. Intente de nuevo en unos segundos."
        if "timeout" in msg:
            return "El proveedor LLM no respondio a tiempo."
        if "connection" in msg or "refused" in msg:
            return "Error de conexion con el proveedor LLM."
        if "auth" in msg or "401" in msg or "403" in msg:
            return "Error de autenticacion. Verifique la API key."
        return f"Error inesperado del proveedor LLM: {str(error)[:200]}"
