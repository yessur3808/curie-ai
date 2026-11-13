"""One local alternate for model refusals; never a tool-permission fallback."""

import os
import re

from llm.chat_prompt import as_chat_prompt


def fallback_reason(response):
    if not isinstance(response, str) or not response.strip():
        return "empty_response"
    if response.startswith("[Error"):
        return "generation_error"
    text = response.strip().replace("’", "'")
    # Access limitations and missing evidence are not model refusals. Do not
    # mistake quoted refusals, tool failures, or useful partial answers for one.
    if re.search(
        r"\b(?:access|permission|approval|credentials?|connect|unavailable|"
        r"verify|verified|evidence|information|context)\b",
        text,
        re.I,
    ):
        return None
    if len(text.split()) <= 100 and re.match(
        r"^(?:(?:I'm sorry|I am sorry|Sorry)[,.]?\s*)?"
        r"I (?:cannot|can't|won't|am unable to) "
        r"(?:help|assist|provide|comply|fulfill|fulfil)\b",
        text,
        re.I,
    ):
        return "model_refusal"
    return None


def enabled():
    return os.getenv("LLM_ALTERNATE_ENABLED", "false").lower() == "true"


def ask_ollama(prompt, *, temperature, max_tokens):
    """Called only by the serialized worker, after releasing its model cache."""
    import httpx

    model = os.getenv("LLM_ALTERNATE_MODEL", "huihui_ai/qwen3.6-abliterated:35b")
    structured = as_chat_prompt(prompt)
    payload = {
        "model": model,
        "messages": structured.messages,
        "stream": False,
        "think": False,
        "keep_alive": 0,
        "options": {
            "num_ctx": 8192,
            "num_predict": min(max_tokens or 640, 8192),
            "temperature": temperature,
        },
    }
    if structured.json_output:
        payload["format"] = structured.json_schema or "json"
    # Fixed loopback endpoint: never inherit cloud URLs or proxy environment.
    with httpx.Client(
        base_url="http://127.0.0.1:11434",
        timeout=180,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        try:
            result = client.post("/api/chat", json=payload)
            result.raise_for_status()
            data = result.json()
            if data.get("error") or not data.get("done"):
                return "[Error: alternate model generation failed]"
            content = data.get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                return "[Error: alternate model returned no answer]"
            return content.strip()
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return "[Error: alternate local model unavailable]"
        finally:
            # Also release residency when a malformed response or timeout occurs.
            try:
                client.post(
                    "/api/generate",
                    json={"model": model, "keep_alive": 0},
                    timeout=10,
                )
            except httpx.HTTPError:
                pass
