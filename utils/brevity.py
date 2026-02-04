# === Anti-repetition & brevity helpers (drop-in) ===
import re
from typing import List

def _cleanup_role_prefixes(text: str) -> str:
    # Remove leading role labels like "Curie:" or "Assistant:"
    t = re.sub(r'^(curie|assistant)\s*:?\s*', '', text.strip(), flags=re.I)
    return t

def _ngram_dedup(text: str, n: int = 3) -> str:
    # Token-level n-gram dedup to remove loops and subtle paraphrase repeats
    tokens = re.findall(r"\S+|\n", text)
    seen = set()
    out = []
    for i in range(len(tokens)):
        window = tuple(tokens[max(0, i-n+1):i+1])
        if len(window) == n and window in seen:
            continue
        if len(window) == n:
            seen.add(window)
        out.append(tokens[i])
    s = ''.join(out)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r' *\n *', '\n', s)
    return s.strip()

def _limit_sentences(text: str, max_sents: int) -> str:
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    parts = [p for p in parts if p]
    if len(parts) > max_sents:
        parts = parts[:max_sents]
    result = ' '.join(parts).strip()
    if result and not re.search(r'[.!?]$', result):
        result += '.'
    return result

def _strip_phantom_tools(text: str) -> str:
    # Replace claims of accessing databases/APIs with a standard line
    patterns = [
        r"\b(access(ed|ing)?|check(ed|ing)?|query(ed|ing)?)\b.*\b(database|api|web(site)?|weather (service|database))\b",
        r"\baccording to the (forecast|database|api|system)\b"
    ]
    if any(re.search(p, text, flags=re.I) for p in patterns):
        sents = re.split(r'(?<=[.!?])\s+', text.strip())
        for i, s in enumerate(sents):
            if any(re.search(p, s, flags=re.I) for p in patterns):
                sents[i] = "I can’t fetch live data right now. Please check an official source for the latest information."
                break
        return ' '.join([x for x in sents if x])
    return text

def finalize_reply(raw: str, max_sents: int) -> str:
    t = _cleanup_role_prefixes(raw)
    t = _strip_phantom_tools(t)
    t = _ngram_dedup(t, n=3)
    t = _limit_sentences(t, max_sents=max_sents)
    return t

def wants_concise(user_text: str) -> bool:
    t = (user_text or "").lower()
    return any(k in t for k in ["be concise", "concise", "brief", "shorter", "tl;dr"])
# === End helpers ===