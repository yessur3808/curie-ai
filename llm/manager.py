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

def ask_llm(prompt, model_name=None, temperature=0.7, max_tokens=128):
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
                stop=["</s>", "User:", "user:"],
                temperature=temperature
            )
            if isinstance(result, dict) and "choices" in result:
                return result["choices"][0]["text"].strip()
            elif hasattr(result, "choices"):
                return result.choices[0].text.strip()
            else:
                return str(result)
        except Exception as e:
            return f"[Error during inference: {e}]"
    else:
        return "[Error: Unsupported LLM provider]"


def get_available_models():
    return AVAILABLE_MODELS

import re

def clean_assistant_reply(reply: str) -> str:
    """
    Removes leading speaker tags (e.g., 'Curie:') and repeated lines.
    """
    # Remove leading speaker tag (Curie:) if present
    reply = reply.strip()
    reply = re.sub(r"^(Curie:|Assistant:)\s*", "", reply, flags=re.IGNORECASE)
    
    # Remove repeated lines (very basic de-duplication)
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    seen = set()
    cleaned_lines = []
    for line in lines:
        if line not in seen:
            cleaned_lines.append(line)
            seen.add(line)
    # Join lines again
    return " ".join(cleaned_lines)