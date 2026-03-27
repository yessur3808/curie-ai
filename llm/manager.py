# llm/manager.py

from __future__ import annotations

import os
import logging
import hashlib
import time
import gc
from collections import OrderedDict
from threading import Event, Lock, Thread
from dotenv import load_dotenv
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from llama_cpp import Llama

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None  # type: ignore

# Load environment variables from .env
load_dotenv()

# Parse models from .env (comma-separated list)
models_env = os.getenv("LLM_MODELS", "")
AVAILABLE_MODELS = [m.strip() for m in models_env.split(",") if m.strip()]

# Fallback default if nothing set in .env
DEFAULT_LLAMA_MODEL = (
    AVAILABLE_MODELS[0]
    if AVAILABLE_MODELS
    else "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
)

# Get config from environment variables
llm_config = {
    "provider": os.getenv("LLM_PROVIDER", "llama.cpp"),
    "model_path": os.getenv(
        "LLM_MODEL", ""
    ),  # Optional: not used if you use LLM_MODELS
    "temperature": float(os.getenv("LLM_TEMPERATURE", 0.7)),
}

# Cache for loaded llama models
llama_models_cache = {}

# Response cache: {prompt_hash: (response, timestamp)}
# TTL: 300 seconds (5 minutes), max_size: 100 entries
_response_cache = OrderedDict()
_response_cache_ttl = 300
_response_cache_max_size = 100
_response_cache_lock = Lock()
_response_cache_hits = 0
_response_cache_misses = 0

# Memory management configuration
MAX_MODELS_IN_CACHE = 1  # Only keep one model in memory at a time
_last_gc_time = 0
_gc_interval = 300  # Run garbage collection every 5 minutes

# Background preload state
# _model_load_event is set once preload_llama_model() finishes (or is skipped/fails).
# ask_llm() waits on it so concurrent callers don't race to load the model.
_model_load_event = Event()
_model_lazy_lock = Lock()  # serialise concurrent lazy-load attempts in ask_llm()
_background_preload_started = False  # True only when start_background_preload() is used
# How long ask_llm() waits for a background preload to finish before giving up.
_PRELOAD_WAIT_TIMEOUT = 300  # seconds


def _trigger_garbage_collection():
    """Periodically trigger garbage collection to free memory."""
    global _last_gc_time
    current_time = time.time()
    if current_time - _last_gc_time > _gc_interval:
        gc.collect()
        _last_gc_time = current_time
        logger.debug("Triggered garbage collection")


def _cleanup_excess_models():
    """
    Keep only the most recently used model in cache.
    Prevents unbounded memory growth from loading multiple models.
    """
    global llama_models_cache
    if len(llama_models_cache) > MAX_MODELS_IN_CACHE:
        # Keep only the first (most recent) model
        excess_models = list(llama_models_cache.keys())[1:]
        for model_name in excess_models:
            logger.info(f"Unloading excess model from cache: {model_name}")
            del llama_models_cache[model_name]
        gc.collect()
        _trigger_garbage_collection()


class ResponseCache:
    """Simple TTL-based cache for LLM responses."""

    @staticmethod
    def _make_key(prompt: str, temperature: float, max_tokens: int) -> str:
        """Create a hash key from prompt + parameters."""
        key_str = f"{prompt}||{temperature}||{max_tokens}"
        return hashlib.md5(key_str.encode()).hexdigest()

    @staticmethod
    def get(prompt: str, temperature: float, max_tokens: int) -> str | None:
        """Get cached response if available and not expired."""
        global _response_cache_hits, _response_cache_misses

        key = ResponseCache._make_key(prompt, temperature, max_tokens)
        with _response_cache_lock:
            if key in _response_cache:
                response, timestamp = _response_cache[key]
                if time.time() - timestamp < _response_cache_ttl:
                    _response_cache_hits += 1
                    logger.debug(f"Response cache hit (key={key[:8]}...)")
                    return response
                else:
                    # Expired, remove it
                    del _response_cache[key]
            _response_cache_misses += 1
        return None

    @staticmethod
    def set(prompt: str, temperature: float, max_tokens: int, response: str):
        """Cache a response."""
        key = ResponseCache._make_key(prompt, temperature, max_tokens)
        with _response_cache_lock:
            _response_cache[key] = (response, time.time())
            # FIFO eviction when exceeding max size
            while len(_response_cache) > _response_cache_max_size:
                _response_cache.popitem(last=False)

    @staticmethod
    def stats() -> dict:
        """Return cache statistics."""
        total = _response_cache_hits + _response_cache_misses
        hit_rate = (_response_cache_hits / total * 100) if total > 0 else 0
        return {
            "hits": _response_cache_hits,
            "misses": _response_cache_misses,
            "hit_rate_percent": round(hit_rate, 1),
            "size": len(_response_cache),
        }


def _select_available_model(preferred: str | None = None) -> str | None:
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(AVAILABLE_MODELS)

    seen = set()
    for model_name in candidates:
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        model_path = os.path.join("models", model_name)
        if os.path.exists(model_path):
            return model_name
    return None


def _load_model_with_fallback(
    preferred: str | None = None,
) -> tuple[Llama | None, str | None]:
    """
    Attempts to load a model, trying fallbacks if the preferred model fails.
    Returns (loaded_model, model_name) or (None, None) if all models fail.
    """
    if Llama is None:
        logger.error("llama_cpp is not installed")
        return None, None

    # Build candidate list
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(AVAILABLE_MODELS)

    # Try each candidate in order
    seen = set()
    # Default to all logical CPU cores rather than a hardcoded magic number.
    n_threads = _get_int_env("LLM_THREADS", os.cpu_count() or 4)
    # GPU layers: number of transformer layers to offload to GPU (0 = CPU-only).
    # Set LLM_GPU_LAYERS=-1 to offload all layers (full GPU inference).
    n_gpu_layers = _get_int_env("LLM_GPU_LAYERS", 0)
    # Suppress llama.cpp's own verbose init output unless DEBUG logging is on.
    verbose = logger.isEnabledFor(logging.DEBUG)

    for model_name in candidates:
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)

        model_path = os.path.join("models", model_name)
        if not os.path.exists(model_path):
            logger.warning(f"Model file not found: {model_path}")
            continue

        logger.info(f"Attempting to load model: {model_name}")
        try:
            model = Llama(
                model_path=model_path,
                n_ctx=MODEL_CONTEXT_SIZE,
                n_threads=n_threads,
                n_gpu_layers=n_gpu_layers,
                verbose=verbose,
            )
            logger.info(f"Successfully loaded model: {model_name}")
            return model, model_name
        except Exception as e:
            logger.error(f"Failed to load {model_name}: {e}")
            continue

    logger.error("All model loading attempts failed")
    return None, None


# Context window and token management configuration from environment variables
# Helper function to safely parse integer environment variables
def _get_int_env(key, default):
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid value '{os.getenv(key)}' for {key}, using default {default}"
        )
        return default


MODEL_CONTEXT_SIZE = _get_int_env(
    "LLM_CONTEXT_SIZE", 2048
)  # Total context window size for llama.cpp models
CONTEXT_BUFFER_TOKENS = _get_int_env(
    "LLM_CONTEXT_BUFFER", 16
)  # Reserve buffer for system tokens, etc.
MIN_AVAILABLE_TOKENS = _get_int_env(
    "LLM_MIN_TOKENS", 64
)  # Minimum tokens required for a meaningful response
FALLBACK_MAX_TOKENS = _get_int_env(
    "LLM_FALLBACK_MAX_TOKENS", 512
)  # Conservative fallback if tokenization fails
DEFAULT_MAX_TOKENS = _get_int_env(
    "LLM_DEFAULT_MAX_TOKENS", 256
)  # Default max_tokens for ask_llm() when the caller does not specify a value (default: 256)


def preload_llama_model():
    """
    Loads the default Llama model into memory at startup.
    Should be called ONCE before any ask_llm calls.
    Skipped automatically when no llama.cpp provider is configured.

    Always sets ``_model_load_event`` on return (whether the model was loaded,
    skipped, or the load failed) so that any ``ask_llm()`` callers waiting on
    the event are unblocked.
    """
    # Honour LLM_PROVIDER_PRIORITY to skip local GGUF loading for cloud-only
    # configurations.  LLM_PROVIDER is treated as a legacy override, but we
    # normalise it to lowercase and only fall back to "llama.cpp" when both
    # LLM_PROVIDER and LLM_PROVIDER_PRIORITY are absent.
    provider_priority = [
        p.strip().lower()
        for p in os.getenv("LLM_PROVIDER_PRIORITY", "").split(",")
        if p.strip()
    ]
    provider = llm_config.get("provider", "").strip().lower()
    # If neither source mentions llama.cpp, skip the local preload.
    if provider_priority:
        # Provider priority is explicitly configured — respect it.
        if "llama.cpp" not in provider_priority:
            logger.info("Skipping llama.cpp model preload (cloud-only provider config)")
            _model_load_event.set()
            return
    elif provider and provider != "llama.cpp":
        # No priority list, but LLM_PROVIDER is set to a non-local value.
        logger.info("Skipping llama.cpp model preload (cloud-only provider config)")
        _model_load_event.set()
        return
    # Else: neither is set (defaults), fall through and load the local model.

    if Llama is None:
        _model_load_event.set()
        raise RuntimeError("llama_cpp is not installed")

    preferred_model = llm_config.get("model_path") or DEFAULT_LLAMA_MODEL
    logger.info(f"Preloading LLM model (preferred: {preferred_model})")

    model, model_name = _load_model_with_fallback(preferred_model)
    if model is None or model_name is None:
        _model_load_event.set()
        raise RuntimeError("Failed to load any available LLM model")

    llama_models_cache[model_name] = model
    logger.info(f"Preloaded model: {model_name}")
    _model_load_event.set()


def _run_preload_background():
    """Worker target for :func:`start_background_preload`."""
    try:
        preload_llama_model()
    except Exception as exc:
        logger.warning("Background LLM preload failed: %s", exc, exc_info=True)
    finally:
        # Always signal — even on failure — so ask_llm() is never blocked forever.
        _model_load_event.set()


def start_background_preload() -> Thread:
    """Load the LLM model in a background daemon thread.

    Returns immediately so that connectors and the workflow can start while
    the model warms up.  The first :func:`ask_llm` call that needs the local
    model will wait (up to 5 minutes) for the preload to finish.

    Returns
    -------
    threading.Thread
        The daemon thread handling the preload (joinable if needed).
    """
    global _background_preload_started
    _background_preload_started = True
    t = Thread(target=_run_preload_background, daemon=True, name="llm-preload")
    t.start()
    logger.info("LLM model preload started in background thread")
    return t


# ---------------------------------------------------------------------------
# Response quality helpers (ML enhancement: detect poor output, enable retry)
# ---------------------------------------------------------------------------

# Minimum word count for a response to be considered non-trivial quality.
_QUALITY_MIN_WORDS = 4


def _response_quality_ok(response: str) -> bool:
    """Return True when *response* passes basic quality checks.

    A failed check triggers a quality-retry in ``ask_llm`` with a higher
    temperature, giving the model a second chance to produce useful output.
    Checks performed:
    - Non-empty and at least ``_QUALITY_MIN_WORDS`` words
    - Does not start with an error sentinel
    - Is not just a sanity-filter apology with no real content
    """
    if not response or len(response.strip()) < 5:
        return False
    if response.startswith("[Error"):
        return False
    words = response.split()
    if len(words) < _QUALITY_MIN_WORDS:
        return False
    # Sanity filter produces short apology strings when output is garbled
    if response.startswith("I apologize") and len(words) < 20:
        return False
    if response.startswith("I'm having trouble") and len(words) < 20:
        return False
    return True


def ask_llm(prompt, model_name=None, temperature=0.7, max_tokens=None):
    """Query the local llama.cpp model.

    Token budget
    ------------
    When *max_tokens* is ``None`` (the default) the function computes the
    maximum tokens available after the prompt:

        available = MODEL_CONTEXT_SIZE - prompt_tokens - CONTEXT_BUFFER_TOKENS

    and uses that as the token budget.  This means the model can produce as
    long a response as the context window allows — no artificial cap is applied.
    Pass an explicit integer only when you intentionally want to constrain the
    response length (e.g. for short summaries).

    Quality retry
    -------------
    If the first inference attempt produces a response that is too short or
    repetitive, the call is retried once with a slightly higher temperature to
    encourage more diverse token generation.
    """
    # Periodically trigger garbage collection
    _trigger_garbage_collection()

    provider = llm_config.get("provider", "llama.cpp")

    if provider == "openai":
        return f"[OpenAI simulated response to]: {prompt}"

    elif provider == "llama.cpp":

        if Llama is None:
            return "[Error: llama_cpp not installed]"

        # Check response cache first — use sentinel 0 for "dynamic" max_tokens key
        _cache_key_tokens = max_tokens if max_tokens is not None else 0
        cached_response = ResponseCache.get(prompt, temperature, _cache_key_tokens)
        if cached_response:
            return cached_response

        # Decide which model filename to use
        preferred_model = (
            model_name or llm_config.get("model_path") or DEFAULT_LLAMA_MODEL
        )

        # Try to use cached model first
        llama_model = None

        # If a background preload is running and hasn't finished yet, wait for
        # it before attempting a lazy-load ourselves.  This avoids two threads
        # racing to load the same GGUF file simultaneously.
        if _background_preload_started and not _model_load_event.is_set():
            logger.info("Waiting for background LLM preload to complete…")
            _model_load_event.wait(timeout=_PRELOAD_WAIT_TIMEOUT)

        # Check if preferred model is already cached
        if preferred_model in llama_models_cache:
            selected_model = preferred_model
            llama_model = llama_models_cache[preferred_model]
        else:
            # Only fall back to a cached model if the requested model file
            # does not exist on disk; otherwise we must load the right model.
            preferred_path = os.path.join("models", preferred_model)
            if not os.path.exists(preferred_path):
                for cached_name in llama_models_cache:
                    selected_model = cached_name
                    llama_model = llama_models_cache[cached_name]
                    logger.info(
                        f"Preferred model not found on disk; using cached model: {cached_name}"
                    )
                    break

        # Lazy-load with fallback if no cached model available.
        # The lock prevents multiple concurrent callers from each trying to
        # load the model file at the same time (e.g. first burst of messages).
        if llama_model is None:
            with _model_lazy_lock:
                # Re-check inside the lock — another thread may have loaded it.
                if preferred_model in llama_models_cache:
                    llama_model = llama_models_cache[preferred_model]
                    selected_model = preferred_model
                else:
                    for cached_name in llama_models_cache:
                        llama_model = llama_models_cache[cached_name]
                        selected_model = cached_name
                        break
                if llama_model is None:
                    logger.info(
                        f"No cached model, attempting to load with fallback (preferred: {preferred_model})"
                    )
                    model, model_name_loaded = _load_model_with_fallback(preferred_model)
                    if model is None or model_name_loaded is None:
                        return "[Error: Failed to load any available model]"
                    llama_models_cache[model_name_loaded] = model
                    llama_model = model
                    selected_model = model_name_loaded
                    # Clean up excess models to prevent memory bloat
                    _cleanup_excess_models()

        # Compute the effective token budget:
        # - Tokenise the prompt precisely using the loaded model's tokeniser.
        # - When max_tokens is None, use ALL available context-window space so
        #   the response is never artificially truncated.
        try:
            prompt_tokens = len(llama_model.tokenize(prompt.encode("utf-8")))
            available_tokens = (
                MODEL_CONTEXT_SIZE - prompt_tokens - CONTEXT_BUFFER_TOKENS
            )

            if available_tokens < MIN_AVAILABLE_TOKENS:
                return (
                    f"[Error: Prompt too long ({prompt_tokens} tokens). "
                    f"Maximum context is {MODEL_CONTEXT_SIZE} tokens.]"
                )

            if max_tokens is None:
                # Dynamic mode: use all available space
                effective_max_tokens = available_tokens
            else:
                # Caller-specified cap — never exceed available space
                effective_max_tokens = min(max_tokens, available_tokens)

        except Exception as tokenize_exc:
            logger.debug(
                "Tokenization failed (%s); using fallback budget", tokenize_exc
            )
            if max_tokens is None:
                effective_max_tokens = FALLBACK_MAX_TOKENS
            else:
                effective_max_tokens = min(max_tokens, FALLBACK_MAX_TOKENS)

        def _run_inference(temp: float) -> str:
            result = llama_model(
                prompt,
                max_tokens=effective_max_tokens,
                stop=["</s>", "User:", "user:", "\nUser:", "\nuser:"],
                temperature=temp,
                repeat_penalty=1.1,
                top_p=0.95,
                top_k=40,
            )
            if isinstance(result, dict) and "choices" in result:
                raw = result["choices"][0]["text"].strip()
            elif hasattr(result, "choices"):
                raw = result.choices[0].text.strip()
            else:
                raw = str(result)
            return _sanity_filter_response(raw)

        try:
            response = _run_inference(temperature)

            # Quality retry: if the first attempt produces a very short or
            # apology-only response, retry once with a slightly higher temperature
            # to encourage more diverse, complete output.
            if _response_quality_ok(response) is False:
                logger.info(
                    "Response quality check failed (len=%d words); retrying with higher temperature",
                    len(response.split()),
                )
                retry_temp = min(temperature + 0.2, 1.0)
                response2 = _run_inference(retry_temp)
                if _response_quality_ok(response2) or len(response2) > len(response):
                    response = response2

            # Cache the final response
            ResponseCache.set(prompt, temperature, _cache_key_tokens, response)

            return response
        except Exception as e:
            return f"[Error during inference: {e}]"
    else:
        return "[Error: Unsupported LLM provider]"


def get_available_models():
    return AVAILABLE_MODELS


import re


def _sanity_filter_response(response: str) -> str:
    """
    Applies sanity checks to LLM output to prevent nonsense/derailed responses.
    - Checks for excessive repetition
    - Validates minimum coherence
    - Filters gibberish
    """
    if not response or len(response.strip()) == 0:
        return "I'm having trouble formulating a response. Could you rephrase that?"

    # Check for excessive character repetition (e.g., "aaaaaaa" or "hahahaha")
    if re.search(r"(.)\1{10,}", response):
        return "I apologize, I seem to have generated an invalid response. Could you try asking again?"

    # Check for excessive word repetition
    words = response.split()
    if len(words) > 5:
        # Count consecutive repeated words
        max_consecutive = 1
        current_consecutive = 1
        for i in range(1, len(words)):
            if words[i].lower() == words[i - 1].lower():
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 1

        if max_consecutive > 3:
            return "I apologize, I seem to have generated an invalid response. Could you try asking again?"

    # Check for minimum reasonable length (but allow short valid responses)
    if len(response) < 2:
        return "I'm not sure how to respond to that."

    # Note: Non-ASCII check removed to allow multilingual content (French, etc.)
    # Curie uses French phrases like 'C'est intéressant!' which contain accented characters

    # Response passes all sanity checks
    return response


def clean_assistant_reply(reply: str) -> str:
    """
    Removes leading speaker tags, meta-notes, system prompts, and repeated lines.
    Prevents persona leakage and meta-commentary from reaching users.
    """
    # Remove leading speaker tag (Curie:, Assistant:, etc.) if present
    reply = reply.strip()

    # Build speaker tag pattern including custom persona name from environment
    speaker_tags = ["Curie", "Assistant", "AI", "System"]
    custom_persona_name = os.getenv("ASSISTANT_NAME")
    if custom_persona_name and custom_persona_name not in speaker_tags:
        speaker_tags.append(custom_persona_name)

    # Create regex pattern with all speaker tags
    speaker_pattern = (
        r"^(" + "|".join(re.escape(tag) + ":" for tag in speaker_tags) + r")\s*"
    )
    reply = re.sub(speaker_pattern, "", reply, flags=re.IGNORECASE)

    # Remove common meta-note patterns
    # Patterns that span multiple lines (use DOTALL to match . across newlines)
    multiline_patterns = [
        # Bracketed meta-notes
        r"\[Note:.*?\]",  # [Note: ...]
        r"\(Note:.*?\)",  # (Note: ...)
        r"\[System:.*?\]",  # [System: ...]
        r"\[Meta:.*?\]",  # [Meta: ...]
        r"\[Internal:.*?\]",  # [Internal: ...]
        r"\[DEBUG:.*?\]",  # [DEBUG: ...]
        r"\[Reasoning:.*?\]",  # [Reasoning: ...]
        r"\[Thought:.*?\]",  # [Thought: ...]
        r"\[Thinking:.*?\]",  # [Thinking: ...]
        r"\[Analysis:.*?\]",  # [Analysis: ...]
        r"\[Context:.*?\]",  # [Context: ...]
        r"\[Info:.*?\]",  # [Info: ...]
        r"\[Warning:.*?\]",  # [Warning: ...]
        r"\[Error:.*?\]",  # [Error: ...]
        r"\[Response:.*?\]",  # [Response: ...]
        r"\[Output:.*?\]",  # [Output: ...]
        # Parenthetical meta-notes
        r"\(System:.*?\)",  # (System: ...)
        r"\(Meta:.*?\)",  # (Meta: ...)
        r"\(Internal:.*?\)",  # (Internal: ...)
        r"\(Thinking:.*?\)",  # (Thinking: ...)
        r"\(Thought:.*?\)",  # (Thought: ...)
        # XML-style thinking tags
        r"<think>.*?</think>",  # <think>...</think>
        r"<thinking>.*?</thinking>",  # <thinking>...</thinking>
        r"<thought>.*?</thought>",  # <thought>...</thought>
        r"<reasoning>.*?</reasoning>",  # <reasoning>...</reasoning>
        r"<analysis>.*?</analysis>",  # <analysis>...</analysis>
        r"<internal>.*?</internal>",  # <internal>...</internal>
        r"<meta>.*?</meta>",  # <meta>...</meta>
        r"<context>.*?</context>",  # <context>...</context>
        # Instruction markers (various formats)
        r"\[INST\].*?\[/INST\]",  # [INST]...[/INST]
        r"<<INST>>.*?<</INST>>",  # <<INST>>...</INST>>
        r"<\|im_start\|>.*?<\|im_end\|>",  # ChatML format
        r"\[SYSTEM\].*?\[/SYSTEM\]",  # [SYSTEM]...[/SYSTEM]
        r"<<SYS>>.*?<</SYS>>",  # <<SYS>>...</SYS>>
        r"(?m-s)^(?:<s>.*?</s>)$",  # Special tokens on their own line
        r"###\s*Instruction:.*?###",  # ### Instruction: ... ###
        r"###\s*System:.*?###",  # ### System: ... ###
    ]
    for pattern in multiline_patterns:
        reply = re.sub(pattern, "", reply, flags=re.IGNORECASE | re.DOTALL)

    # Line-anchored patterns (use MULTILINE so ^ and $ match line boundaries)
    # These should only remove content on a single line, not span multiple lines
    line_anchored_patterns = [
        r"^\*\*Note:\*\*.*?$",  # **Note:** at start of line
        r"^\*\*System:\*\*.*?$",  # **System:** at start of line
        r"^\*\*Meta:\*\*.*?$",  # **Meta:** at start of line
        r"^Note:.*?$",  # Note: at start of line
        r"^System:.*?$",  # System: at start of line
        r"^Meta:.*?$",  # Meta: at start of line
    ]
    for pattern in line_anchored_patterns:
        reply = re.sub(pattern, "", reply, flags=re.IGNORECASE | re.MULTILINE)

    # Remove lines that look like system prompts or instructions
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    filtered_lines = []
    for line in lines:
        # Skip lines that look like instructions/prompts
        lower_line = line.lower()
        if any(
            marker in lower_line
            for marker in [
                # Identity/role instructions
                "you are an",
                "you are a",
                "you are the",
                "your name is",
                "your role is",
                "you must",
                "you should",
                "you will",
                "you have to",
                "you need to",
                # System/instruction markers
                "system:",
                "instruction:",
                "instructions:",
                "assistant:",
                "user:",
                "human:",
                # Behavioral directives
                "respond as",
                "act as",
                "roleplay as",
                "pretend to be",
                "behave as",
                "speak as",
                "reply as",
                "answer as",
                # Rule/guideline indicators
                "here are the rules",
                "follow these guidelines",
                "follow these rules",
                "follow these instructions",
                "according to the rules",
                "as per the guidelines",
                "you must follow",
                "remember to",
                "make sure to",
                "always remember",
                "never forget",
                # Meta-commentary about response
                "i am programmed to",
                "i am designed to",
                "i am an ai",
                "i am a language model",
                "as an ai",
                "as a language model",
                "my programming",
                "my instructions",
                # Prompt leakage indicators
                "in this scenario",
                "in this roleplay",
                "for this conversation",
                "in this context",
                "based on my prompt",
                "according to my prompt",
            ]
        ):
            continue
        filtered_lines.append(line)

    # Remove repeated lines (basic de-duplication)
    seen = set()
    cleaned_lines = []
    for line in filtered_lines:
        if line not in seen:
            cleaned_lines.append(line)
            seen.add(line)

    # Join lines again
    result = " ".join(cleaned_lines)

    # Final cleanup: remove excessive whitespace
    result = re.sub(r"\s+", " ", result).strip()

    return result
