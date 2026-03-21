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
            logger.warning(
                "Unknown provider %r in LLM_PROVIDER_PRIORITY — skipping", provider
            )
    return available


# ---------------------------------------------------------------------------
# Cloud provider helpers
# ---------------------------------------------------------------------------

# Default max_tokens for cloud APIs when caller does not specify a limit.
# Anthropic requires the field; OpenAI/Gemini omit it when None to use model defaults.
_CLOUD_DEFAULT_MAX_TOKENS = 4096


def _call_openai(
    prompt: str, temperature: float, max_tokens: Optional[int]
) -> Optional[str]:
    """Call OpenAI Chat Completions API."""
    try:
        import openai  # type: ignore
    except ImportError:
        logger.error("openai package not installed; run: pip install openai")
        return None

    client = openai.OpenAI(api_key=_env("OPENAI_API_KEY"))
    model = _env("OPENAI_MODEL", "gpt-3.5-turbo")

    try:
        kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content or ""
        logger.debug(
            "OpenAI response received (model=%s, tokens=%s)",
            model,
            getattr(response.usage, "total_tokens", "?"),
        )
        return text.strip()
    except Exception as exc:
        logger.warning("OpenAI call failed: %s", exc)
        return None


def _call_anthropic(
    prompt: str, temperature: float, max_tokens: Optional[int]
) -> Optional[str]:
    """Call Anthropic Messages API.

    Anthropic requires ``max_tokens`` to be set; defaults to
    ``_CLOUD_DEFAULT_MAX_TOKENS`` when the caller passes ``None``.
    """
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
            max_tokens=(
                max_tokens if max_tokens is not None else _CLOUD_DEFAULT_MAX_TOKENS
            ),
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text if message.content else ""
        logger.debug("Anthropic response received (model=%s)", model)
        return text.strip()
    except Exception as exc:
        logger.warning("Anthropic call failed: %s", exc)
        return None


def _call_gemini(
    prompt: str, temperature: float, max_tokens: Optional[int]
) -> Optional[str]:
    """Call Google Gemini GenerateContent API."""
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError:
        logger.error(
            "google-generativeai package not installed; run: pip install google-generativeai"
        )
        return None

    genai.configure(api_key=_env("GOOGLE_API_KEY"))
    model_name = _env("GEMINI_MODEL", "gemini-1.5-flash")

    try:
        model = genai.GenerativeModel(model_name)
        gen_config_kwargs: dict = {"temperature": temperature}
        if max_tokens is not None:
            gen_config_kwargs["max_output_tokens"] = max_tokens
        gen_config = genai.types.GenerationConfig(**gen_config_kwargs)
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
# Semantic query complexity classification (no word-count thresholds)
# ---------------------------------------------------------------------------
#
# How routing works
# -----------------
# When both cloud and local providers are active, the cost-saving heuristic
# routes *trivially simple* queries (greetings, one-word pleasantries) to the
# local model first so they never consume cloud API quota.  Everything else —
# especially any task that mentions planning, scheduling, coding, analysis, etc.
# — follows the configured LLM_PROVIDER_PRIORITY order unchanged.
#
# This classification is PURELY SEMANTIC:  no word-count thresholds are used.
# A 3-word greeting like "hi how are you" is simple; a 3-word task like
# "plan a trip" is complex and always follows priority order.

# Exact full-string patterns for definitively trivial queries.
# Uses re.FULLMATCH so "hi, plan my trip" is NOT matched as simple.
# The `+` quantifier on greeting words allows elongated spellings uniformly
# (hiii, hellooo, heyyyy) — applied to the last letter of each word.
_SIMPLE_PATTERNS = re.compile(
    r"hi+[!. ]*"
    r"|hell[o]+[!. ]*"
    r"|he[y]+[!. ]*"
    r"|howdy[!. ]*"
    r"|good\s+(?:morning|afternoon|evening|night)[!. ]*"
    r"|how\s+are\s+you[\?!. ]*"
    r"|how(?:'s|\s+is)\s+it\s+going[\?!. ]*"
    r"|who\s+are\s+you[\?!. ]*"
    r"|what(?:'s|\s+is)\s+your\s+name[\?!. ]*"
    r"|thank(?:s|\s+you)(?:\s+(?:a\s+lot|so\s+much|very\s+much))?[!. ]*"
    r"|by[e]+(?:[\s\-]bye)?[!. ]*"
    r"|goodbye[!. ]*"
    r"|ok(?:ay)?[!. ]*"
    r"|what(?:'s|\s+is)\s+(?:the\s+)?(?:time|date|day)(?:\s+(?:today|now))?[\?!. ]*"
    r"|what(?:'s|\s+is)\s+(?:the\s+)?weather(?:\s+(?:today|now|like))?[\?!. ]*",
    re.IGNORECASE,
)

# Keywords that flag a COMPLEX task — queries matching any of these always
# follow the configured priority order (never moved to local-first).
_COMPLEX_PATTERNS = re.compile(
    r"\b(?:"
    # Planning & scheduling
    r"plan|planning|schedule|calendar|agenda|appointment|meeting|event|"
    r"remind|reminder|alarm|alert|"
    # Travel
    r"trip|vacation|holiday|travel|visit|journey|tour|itinerary|getaway|"
    r"flight|hotel|booking|pack|packing|destination|"
    # Technical / coding
    r"code|program|debug|script|function|implement|algorithm|class|"
    r"fix|error|bug|develop|build|deploy|"
    # Analysis / writing
    r"analy[sz]e|research|compare|evaluate|review|assess|"
    r"write|generate|create|draft|compose|design|"
    # Information / conversion
    r"translate|summariz[ei]|convert|calculate|compute|"
    # Navigation
    r"navigate|directions?|route|distance|"
    # Recommendations
    r"recommend|suggest|advic[ei]|advise|"
    # Finance / forecasting
    r"budget|invest|forecast|predict|estimate" r")\b",
    re.IGNORECASE,
)


def _is_simple_query(prompt: str) -> bool:
    """Return True if the prompt is a trivially simple greeting or pleasantry.

    Classification is **purely semantic** — no word-count threshold is applied.
    A 3-word task request like "plan a trip" returns False immediately because
    it matches ``_COMPLEX_PATTERNS``.  Only exact, short-answer phrases (greetings,
    "what's the time?", etc.) that fully match ``_SIMPLE_PATTERNS`` return True.
    """
    stripped = prompt.strip()
    # Complex task keywords always take priority — never considered simple.
    if _COMPLEX_PATTERNS.search(stripped):
        return False
    # Check for full-string trivial greeting/simple-question patterns.
    return bool(_SIMPLE_PATTERNS.fullmatch(stripped))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask_best_provider(
    prompt: str,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    force_provider: Optional[str] = None,
) -> Optional[str]:
    """
    Query the highest-priority available LLM provider.

    How it works
    ------------
    **With multiple providers configured** (e.g. ``LLM_PROVIDER_PRIORITY=anthropic,llama.cpp``):

    * Cloud providers (OpenAI, Anthropic, Gemini) are tried in priority order.
    * If ``LLM_CLOUD_SIMPLE_TASKS=false`` (default), trivially simple queries
      (greetings, "what time is it?") are sent to the local model first to save
      cloud API costs.  Complex tasks (planning, coding, scheduling, …) always
      follow the configured priority and are never silently downgraded to local.
    * If the highest-priority provider fails or its API key is missing, the next
      one in the list is tried automatically.

    **Local-only** (``LLM_PROVIDER_PRIORITY=llama.cpp``, default):

    * Only llama.cpp is active.  The routing and cost-saving logic is skipped.
    * All tasks — including trip planning, reminders, and general chat — are
      handled by the local model.  Skills automatically select compact, efficient
      prompts and use all available context-window space for the response.

    Token budget
    ------------
    When ``max_tokens`` is ``None`` (the default), cloud APIs use their own
    model defaults (generous), and the local llama.cpp manager computes the
    maximum safe token count from the actual prompt length and context window —
    so responses are never artificially truncated.

    Returns
    -------
    str | None
        The response text, or ``None`` if all providers fail.
    """
    use_cloud_for_simple = _env("LLM_CLOUD_SIMPLE_TASKS", "false").lower() == "true"

    if force_provider:
        providers_to_try = [force_provider]
    else:
        providers_to_try = get_active_providers()
        if not providers_to_try:
            logger.error(
                "No LLM providers available; check API keys and installed packages"
            )
            return None

        # Route trivially simple queries to local model first to save API costs.
        # Complex tasks (planning, scheduling, coding, etc.) are never downgraded.
        if not use_cloud_for_simple and _is_simple_query(prompt):
            if "llama.cpp" in providers_to_try:
                providers_to_try = ["llama.cpp"] + [
                    p for p in providers_to_try if p != "llama.cpp"
                ]

    for provider in providers_to_try:
        logger.debug("Trying provider: %s", provider)

        if provider in CLOUD_CALLERS:
            result = CLOUD_CALLERS[provider](prompt, temperature, max_tokens)
            if result is not None:
                return result
        elif provider == "llama.cpp":
            # Delegate to the local manager.  Passing max_tokens=None lets the
            # manager compute all available context-window space dynamically.
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
    return {provider: check() for provider, check in PROVIDER_CHECKS.items()}


def is_local_only() -> bool:
    """Return True when the only active provider is llama.cpp (no cloud keys set).

    Skills use this to choose between verbose cloud-optimised prompts and
    compact prompts that fit inside a typical local model's context window.
    """
    active = get_active_providers()
    return len(active) == 1 and active[0] == "llama.cpp"


# Rough token-per-word approximation; used when the model is not yet loaded.
_AVG_TOKENS_PER_WORD = 1.3
# Safety buffer on top of the prompt estimate (system artefacts, special tokens, …)
_PROMPT_TOKEN_BUFFER = 32


def compute_response_budget(prompt: str, max_cap: Optional[int] = None) -> int:
    """Estimate the available token budget for a response to *prompt*.

    Uses a lightweight word-count heuristic so the model does not need to be
    loaded at call time.  The result is floored at 64 so the model always has
    room to produce a meaningful reply.

    When *max_cap* is ``None`` the full available context window is returned
    (minus the estimated prompt size and a safety buffer), giving the model
    maximum space to produce a complete response.

    Parameters
    ----------
    prompt:
        The full prompt that will be sent to the LLM.
    max_cap:
        Optional upper bound.  Pass an integer to constrain the response length
        (e.g. for summarisation tasks).  Pass ``None`` (default) for no cap —
        the model uses all available space.

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
    available = max(64, context_size - estimated_prompt_tokens - _PROMPT_TOKEN_BUFFER)
    if max_cap is not None:
        available = min(available, max_cap)
    return available
