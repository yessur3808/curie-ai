# llm/manager.py

import os
import logging
import hashlib
import time
import gc
from collections import OrderedDict
from threading import Lock
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

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
            "size": len(_response_cache)
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


def _load_model_with_fallback(preferred: str | None = None) -> tuple[Llama | None, str | None]:
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
    n_threads = _get_int_env("LLM_THREADS", 18)
    
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
        logger.warning(f"Invalid value '{os.getenv(key)}' for {key}, using default {default}")
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
    "LLM_DEFAULT_MAX_TOKENS", 128
)  # Conservative default for max_tokens in ask_llm()


def preload_llama_model():
    """
    Loads the default Llama model into memory at startup.
    Should be called ONCE before any ask_llm calls.
    """
    provider = llm_config.get("provider", "llama.cpp")
    if provider != "llama.cpp":
        return  # Only preload local Llama models

    if Llama is None:
        raise RuntimeError("llama_cpp is not installed")

    preferred_model = llm_config.get("model_path") or DEFAULT_LLAMA_MODEL
    logger.info(f"Preloading LLM model (preferred: {preferred_model})")
    
    model, model_name = _load_model_with_fallback(preferred_model)
    if model is None or model_name is None:
        raise RuntimeError("Failed to load any available LLM model")
    
    llama_models_cache[model_name] = model
    logger.info(f"Preloaded model: {model_name}")


def ask_llm(prompt, model_name=None, temperature=0.7, max_tokens=None):
    # Use environment-configured default if max_tokens not specified
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS

    # Periodically trigger garbage collection
    _trigger_garbage_collection()

    provider = llm_config.get("provider", "llama.cpp")

    if provider == "openai":
        return f"[OpenAI simulated response to]: {prompt}"

    elif provider == "llama.cpp":

        if Llama is None:
            return "[Error: llama_cpp not installed]"

        # Check response cache first
        cached_response = ResponseCache.get(prompt, temperature, max_tokens)
        if cached_response:
            return cached_response

        # Decide which model filename to use
        preferred_model = (
            model_name or llm_config.get("model_path") or DEFAULT_LLAMA_MODEL
        )

        # Try to use cached model first
        llama_model = None
        
        # Check if preferred model is already cached
        if preferred_model in llama_models_cache:
            selected_model = preferred_model
            llama_model = llama_models_cache[preferred_model]
        else:
            # Check if any model is cached
            for cached_name in llama_models_cache:
                selected_model = cached_name
                llama_model = llama_models_cache[cached_name]
                logger.info(f"Using cached model: {cached_name}")
                break
        
        # Lazy-load with fallback if no cached model available
        if llama_model is None:
            logger.info(f"No cached model, attempting to load with fallback (preferred: {preferred_model})")
            model, model_name_loaded = _load_model_with_fallback(preferred_model)
            if model is None or model_name_loaded is None:
                return "[Error: Failed to load any available model]"
            llama_models_cache[model_name_loaded] = model
            llama_model = model
            selected_model = model_name_loaded
            # Clean up excess models to prevent memory bloat
            _cleanup_excess_models()

        # Dynamically cap max_tokens to avoid exceeding context window
        # Calculate prompt tokens and ensure prompt_tokens + max_tokens <= n_ctx
        try:
            prompt_tokens = len(llama_model.tokenize(prompt.encode("utf-8")))
            available_tokens = (
                MODEL_CONTEXT_SIZE - prompt_tokens - CONTEXT_BUFFER_TOKENS
            )

            # Cap max_tokens to available space, with a minimum threshold
            if available_tokens < MIN_AVAILABLE_TOKENS:
                # Prompt is too long, return error
                return f"[Error: Prompt too long ({prompt_tokens} tokens). Maximum context is {MODEL_CONTEXT_SIZE} tokens.]"

            capped_max_tokens = min(max_tokens, available_tokens)
        except Exception as e:
            # If tokenization fails, use a conservative default
            capped_max_tokens = min(max_tokens, FALLBACK_MAX_TOKENS)

        try:
            result = llama_model(
                prompt,
                max_tokens=capped_max_tokens,
                stop=["</s>", "User:", "user:", "\nUser:", "\nuser:"],
                temperature=temperature,
                repeat_penalty=1.1,  # Prevent repetition
                top_p=0.95,  # Nucleus sampling for better coherence
                top_k=40,  # Limit token selection for quality
            )
            if isinstance(result, dict) and "choices" in result:
                raw_response = result["choices"][0]["text"].strip()
            elif hasattr(result, "choices"):
                raw_response = result.choices[0].text.strip()
            else:
                raw_response = str(result)

            # Apply sanity filter
            response = _sanity_filter_response(raw_response)
            
            # Cache response
            ResponseCache.set(prompt, temperature, max_tokens, response)
            
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
