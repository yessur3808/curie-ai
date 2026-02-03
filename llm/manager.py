# llm/manager.py

import os
from dotenv import load_dotenv

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


# Context window and token management configuration from environment variables
# Helper function to safely parse integer environment variables
def _get_int_env(key, default):
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        print(f"Warning: Invalid value for {key}, using default {default}")
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

    selected_model = llm_config.get("model_path") or DEFAULT_LLAMA_MODEL
    if selected_model not in AVAILABLE_MODELS:
        raise RuntimeError(f"Model {selected_model} not in AVAILABLE_MODELS")
    model_path = os.path.join("models", selected_model)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")

    if selected_model not in llama_models_cache:
        llama_models_cache[selected_model] = Llama(
            model_path=model_path,
            n_ctx=MODEL_CONTEXT_SIZE,
            n_threads=18,  # Adjust to your CPU
        )


def ask_llm(prompt, model_name=None, temperature=0.7, max_tokens=None):
    # Use environment-configured default if max_tokens not specified
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS

    provider = llm_config.get("provider", "llama.cpp")

    if provider == "openai":
        return f"[OpenAI simulated response to]: {prompt}"

    elif provider == "llama.cpp":

        # Decide which model filename to use
        selected_model = (
            model_name or llm_config.get("model_path") or DEFAULT_LLAMA_MODEL
        )
        if selected_model not in AVAILABLE_MODELS:
            return f"[Model not found in AVAILABLE_MODELS: {selected_model}]"
        model_path = os.path.join("models", selected_model)

        if not os.path.exists(model_path):
            return f"[Model file not found: {model_path}]"

        # Lazy-load and cache each model separately
        if selected_model not in llama_models_cache:
            try:
                llama_models_cache[selected_model] = Llama(
                    model_path=model_path, n_ctx=MODEL_CONTEXT_SIZE, n_threads=18
                )
            except Exception as e:
                return f"[Error loading model: {e}]"
        llama_model = llama_models_cache[selected_model]

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
            return _sanity_filter_response(raw_response)
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
    custom_persona_name = os.getenv("DEFAULT_PERSONA_NAME")
    if custom_persona_name and custom_persona_name not in speaker_tags:
        speaker_tags.append(custom_persona_name)

    # Create regex pattern with all speaker tags
    speaker_pattern = (
        r"^(" + "|".join(re.escape(tag) + ":" for tag in speaker_tags) + r")\s*"
    )
    reply = re.sub(speaker_pattern, "", reply, flags=re.IGNORECASE)

    # Remove common meta-note patterns
    meta_patterns = [
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
        r"<s>.*?</s>",  # Special tokens
        r"###\s*Instruction:.*?###",  # ### Instruction: ... ###
        r"###\s*System:.*?###",  # ### System: ... ###
        # Common prefixes that indicate meta-commentary
        r"^\*\*Note:\*\*.*?$",  # **Note:** at start of line
        r"^\*\*System:\*\*.*?$",  # **System:** at start of line
        r"^\*\*Meta:\*\*.*?$",  # **Meta:** at start of line
        r"^Note:.*?$",  # Note: at start of line
        r"^System:.*?$",  # System: at start of line
        r"^Meta:.*?$",  # Meta: at start of line
    ]
    for pattern in meta_patterns:
        reply = re.sub(pattern, "", reply, flags=re.IGNORECASE | re.DOTALL)

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
