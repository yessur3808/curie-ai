# llm/providers.py
"""
Multi-provider LLM abstraction layer.

Supports:
- llama.cpp  (local GGUF models — always available as fallback)
- OpenAI     (GPT-3.5, GPT-4, o1, …)
- Anthropic  (Claude 3 Haiku/Sonnet/Opus, …)
- Google     (Gemini Pro/Flash, …)

Provider priority is determined by LLM_PROVIDER_PRIORITY (comma-separated list).
When a provider fails or is unavailable its key is missing, the next one is tried.

Environment variables
---------------------
LLM_PROVIDER_PRIORITY   Ordered list of providers to try, e.g. "anthropic,openai,gemini,llama.cpp"
                        Default: "llama.cpp"

# OpenAI
OPENAI_API_KEY          OpenAI API key
OPENAI_MODEL            Model name (default: gpt-3.5-turbo)

# Anthropic
ANTHROPIC_API_KEY       Anthropic API key
ANTHROPIC_MODEL         Model name (default: claude-3-haiku-20240307)

# Google Gemini
GOOGLE_API_KEY          Google AI API key
GEMINI_MODEL            Model name (default: gemini-1.5-flash)

# Ensemble / routing
LLM_CLOUD_SIMPLE_TASKS  Use cloud for simple tasks too (default: false)
                        When false, simple/short queries are handled locally to save API costs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _provider_priority() -> list[str]:
    raw = _env("LLM_PROVIDER_PRIORITY", "llama.cpp")
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Provider availability checks
# ---------------------------------------------------------------------------

def _openai_available() -> bool:
    return bool(_env("OPENAI_API_KEY"))


def _anthropic_available() -> bool:
    return bool(_env("ANTHROPIC_API_KEY"))


def _gemini_available() -> bool:
    return bool(_env("GOOGLE_API_KEY"))


def _llama_available() -> bool:
    try:
        from llama_cpp import Llama  # noqa: F401
        return True
    except ImportError:
        return False


PROVIDER_CHECKS = {
    "openai": _openai_available,
    "anthropic": _anthropic_available,
    "gemini": _gemini_available,
    "llama.cpp": _llama_available,
}


def get_active_providers() -> list[str]:
    """Return ordered list of providers that have credentials/libraries available."""
    available = []
    for provider in _provider_priority():
        check = PROVIDER_CHECKS.get(provider)
        if check and check():
            available.append(provider)
        elif provider not in PROVIDER_CHECKS:
            logger.warning("Unknown provider %r in LLM_PROVIDER_PRIORITY — skipping", provider)
    return available


# ---------------------------------------------------------------------------
# Cloud provider helpers
# ---------------------------------------------------------------------------

def _call_openai(prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
    """Call OpenAI Chat Completions API."""
    try:
        import openai  # type: ignore
    except ImportError:
        logger.error("openai package not installed; run: pip install openai")
        return None

    client = openai.OpenAI(api_key=_env("OPENAI_API_KEY"))
    model = _env("OPENAI_MODEL", "gpt-3.5-turbo")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content or ""
        logger.debug("OpenAI response received (model=%s, tokens=%s)", model,
                     getattr(response.usage, "total_tokens", "?"))
        return text.strip()
    except Exception as exc:
        logger.warning("OpenAI call failed: %s", exc)
        return None


def _call_anthropic(prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
    """Call Anthropic Messages API."""
    try:
        import anthropic  # type: ignore
    except ImportError:
        logger.error("anthropic package not installed; run: pip install anthropic")
        return None

    client = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    model = _env("ANTHROPIC_MODEL", "claude-3-haiku-20240307")

    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text if message.content else ""
        logger.debug("Anthropic response received (model=%s)", model)
        return text.strip()
    except Exception as exc:
        logger.warning("Anthropic call failed: %s", exc)
        return None


def _call_gemini(prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
    """Call Google Gemini GenerateContent API."""
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError:
        logger.error("google-generativeai package not installed; run: pip install google-generativeai")
        return None

    genai.configure(api_key=_env("GOOGLE_API_KEY"))
    model_name = _env("GEMINI_MODEL", "gemini-1.5-flash")

    try:
        model = genai.GenerativeModel(model_name)
        gen_config = genai.types.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        response = model.generate_content(prompt, generation_config=gen_config)
        text = response.text if hasattr(response, "text") else ""
        logger.debug("Gemini response received (model=%s)", model_name)
        return text.strip()
    except Exception as exc:
        logger.warning("Gemini call failed: %s", exc)
        return None


CLOUD_CALLERS = {
    "openai": _call_openai,
    "anthropic": _call_anthropic,
    "gemini": _call_gemini,
}

# ---------------------------------------------------------------------------
# Query complexity heuristic
# ---------------------------------------------------------------------------

_SIMPLE_QUERY_MAX_WORDS = 30
_SIMPLE_KEYWORDS = re.compile(
    r"\b(hi|hello|hey|thanks|thank you|bye|good morning|good night|what time|what day|"
    r"how are you|who are you|your name|weather|date)\b",
    re.IGNORECASE,
)


def _is_simple_query(prompt: str) -> bool:
    """Return True if the query is short *and* matches simple greeting/short-answer patterns.

    Keyword matching is intentionally restricted to prompts that are themselves
    short (≤ _SIMPLE_QUERY_MAX_WORDS words).  A long skill prompt that happens to
    mention "weather" or "date" (e.g. a detailed trip-planning prompt) must NOT be
    misclassified as simple — doing so would route it to the local model even when
    a cloud provider is configured with higher priority.
    """
    words = prompt.split()
    if len(words) > _SIMPLE_QUERY_MAX_WORDS:
        # Long prompts are always considered complex regardless of keywords.
        return False
    # For short prompts, accept either the keyword match or the word-count heuristic.
    return bool(_SIMPLE_KEYWORDS.search(prompt))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_best_provider(
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 512,
    force_provider: Optional[str] = None,
) -> Optional[str]:
    """
    Query the highest-priority available LLM provider.

    When ``LLM_CLOUD_SIMPLE_TASKS`` is ``false`` (the default) and only cloud
    providers are active, simple queries are routed to the local llama.cpp
    model (if available) to reduce API costs.

    Returns the response string, or ``None`` if all providers fail.
    """
    use_cloud_for_simple = _env("LLM_CLOUD_SIMPLE_TASKS", "false").lower() == "true"

    if force_provider:
        providers_to_try = [force_provider]
    else:
        providers_to_try = get_active_providers()
        if not providers_to_try:
            logger.error("No LLM providers available; check API keys and installed packages")
            return None

        # Route simple queries to local model first to save API costs
        if not use_cloud_for_simple and _is_simple_query(prompt):
            if "llama.cpp" in providers_to_try:
                providers_to_try = ["llama.cpp"] + [p for p in providers_to_try if p != "llama.cpp"]

    for provider in providers_to_try:
        logger.debug("Trying provider: %s", provider)

        if provider in CLOUD_CALLERS:
            result = CLOUD_CALLERS[provider](prompt, temperature, max_tokens)
            if result is not None:
                return result
        elif provider == "llama.cpp":
            # Delegate to the existing local manager (avoids duplicating logic)
            try:
                from llm import manager as _local_manager
                result = _local_manager.ask_llm(
                    prompt, temperature=temperature, max_tokens=max_tokens
                )
                if result and not result.startswith("[Error"):
                    return result
            except Exception as exc:
                logger.warning("llama.cpp call failed: %s", exc)
        else:
            logger.warning("No caller registered for provider %r", provider)

    logger.error("All LLM providers failed for this request")
    return None


def provider_status() -> dict:
    """Return availability status for all known providers."""
    return {
        provider: check()
        for provider, check in PROVIDER_CHECKS.items()
    }


def is_local_only() -> bool:
    """Return True when the only active provider is llama.cpp (no cloud keys set).

    Skills can use this to choose between verbose cloud-optimised prompts and
    compact prompts that fit inside a typical local model's context window.
    """
    active = get_active_providers()
    return active == ["llama.cpp"] or (len(active) == 1 and "llama.cpp" in active)


# Rough token-per-word approximation; used when the model is not yet loaded.
_AVG_TOKENS_PER_WORD = 1.3
# Safety buffer on top of the prompt estimate (system artefacts, special tokens, …)
_PROMPT_TOKEN_BUFFER = 32


def compute_response_budget(prompt: str, max_cap: int = 512) -> int:
    """Estimate a safe ``max_tokens`` value for a response to *prompt*.

    Uses a lightweight word-count heuristic so the model does not need to be
    loaded at call time.  The result is capped at *max_cap* and floored at 64
    so the model always has room to produce a meaningful reply.

    This avoids the common problem of hardcoding ``max_tokens=512`` when the
    prompt already occupies most of the context window.

    Parameters
    ----------
    prompt:
        The full prompt that will be sent to the LLM.
    max_cap:
        Upper bound on the returned value.  Caller should set this to the
        maximum response length they want even when the context window is large.

    Returns
    -------
    int
        Estimated safe value for ``max_tokens``.
    """
    try:
        from llm.manager import MODEL_CONTEXT_SIZE  # lazy import avoids circular dep
        context_size = MODEL_CONTEXT_SIZE
    except Exception:
        context_size = 2048  # conservative fallback

    estimated_prompt_tokens = int(len(prompt.split()) * _AVG_TOKENS_PER_WORD)
    available = context_size - estimated_prompt_tokens - _PROMPT_TOKEN_BUFFER
    return max(64, min(max_cap, available))
