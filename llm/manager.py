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
DEFAULT_LLAMA_MODEL = AVAILABLE_MODELS[0] if AVAILABLE_MODELS else "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"

# Get config from environment variables
llm_config = {
    "provider": os.getenv("LLM_PROVIDER", "llama.cpp"),
    "model_path": os.getenv("LLM_MODEL", ""),  # Optional: not used if you use LLM_MODELS
    "temperature": float(os.getenv("LLM_TEMPERATURE", 0.7))
}

# Cache for loaded llama models
llama_models_cache = {}


def preload_llama_model():
    """
    Loads the default Llama model into memory at startup.
    Should be called ONCE before any ask_llm calls.
    """
    provider = llm_config.get('provider', 'llama.cpp')
    if provider != 'llama.cpp':
        return  # Only preload local Llama models

    selected_model = llm_config.get('model_path') or DEFAULT_LLAMA_MODEL
    if selected_model not in AVAILABLE_MODELS:
        raise RuntimeError(f"Model {selected_model} not in AVAILABLE_MODELS")
    model_path = os.path.join("models", selected_model)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")

    if selected_model not in llama_models_cache:
        llama_models_cache[selected_model] = Llama(
            model_path=model_path,
            n_ctx=2048,
            n_threads=18  # Adjust to your CPU
        )

def ask_llm(prompt, model_name=None, temperature=0.7, max_tokens=2048):
    provider = llm_config.get('provider', 'llama.cpp')

    if provider == 'openai':
        return f"[OpenAI simulated response to]: {prompt}"

    elif provider == 'llama.cpp':

        # Decide which model filename to use
        selected_model = model_name or llm_config.get('model_path') or DEFAULT_LLAMA_MODEL
        if selected_model not in AVAILABLE_MODELS:
            return f"[Model not found in AVAILABLE_MODELS: {selected_model}]"
        model_path = os.path.join("models", selected_model)

        if not os.path.exists(model_path):
            return f"[Model file not found: {model_path}]"

        # Lazy-load and cache each model separately
        if selected_model not in llama_models_cache:
            try:
                llama_models_cache[selected_model] = Llama(
                    model_path=model_path,
                    n_ctx=2048,
                    n_threads=18
                )
            except Exception as e:
                return f"[Error loading model: {e}]"
        llama_model = llama_models_cache[selected_model]

        try:
            result = llama_model(
                prompt,
                max_tokens=max_tokens,
                stop=["</s>", "User:", "user:", "\nUser:", "\nuser:"],
                temperature=temperature,
                repeat_penalty=1.1,  # Prevent repetition
                top_p=0.95,  # Nucleus sampling for better coherence
                top_k=40  # Limit token selection for quality
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
    if re.search(r'(.)\1{10,}', response):
        return "I apologize, I seem to have generated an invalid response. Could you try asking again?"
    
    # Check for excessive word repetition
    words = response.split()
    if len(words) > 5:
        # Count consecutive repeated words
        max_consecutive = 1
        current_consecutive = 1
        for i in range(1, len(words)):
            if words[i].lower() == words[i-1].lower():
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
    reply = re.sub(r"^(Curie:|Assistant:|AI:|System:)\s*", "", reply, flags=re.IGNORECASE)
    
    # Remove common meta-note patterns
    meta_patterns = [
        r'\[Note:.*?\]',  # [Note: ...]
        r'\(Note:.*?\)',  # (Note: ...)
        r'\[System:.*?\]',  # [System: ...]
        r'\[Meta:.*?\]',  # [Meta: ...]
        r'\[Internal:.*?\]',  # [Internal: ...]
        r'\[DEBUG:.*?\]',  # [DEBUG: ...]
        r'\[Reasoning:.*?\]',  # [Reasoning: ...]
        r'<think>.*?</think>',  # <think>...</think>
        r'\[INST\].*?\[/INST\]',  # Instruction markers
        r'<<SYS>>.*?<</SYS>>',  # System markers
    ]
    for pattern in meta_patterns:
        reply = re.sub(pattern, '', reply, flags=re.IGNORECASE | re.DOTALL)
    
    # Remove lines that look like system prompts or instructions
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    filtered_lines = []
    for line in lines:
        # Skip lines that look like instructions/prompts
        lower_line = line.lower()
        if any(marker in lower_line for marker in [
            'you are an', 'your name is', 'you must', 'system:', 
            'instruction:', 'respond as', 'act as', 'roleplay as',
            'here are the rules', 'follow these guidelines'
        ]):
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
    result = re.sub(r'\s+', ' ', result).strip()
    
    return result