"""Personality controls wording and restrained delivery; weights control accent."""

import hashlib
import json
import re
from collections.abc import Mapping

_HISTORY_WORD = re.compile(r"[a-z0-9][a-z0-9'_-]{2,}", re.IGNORECASE)
_HISTORY_STOPWORDS = frozenset(
    "about affect after again also and are because been before being can could did does "
    "doing for from get got had has have how into its just like make more much not "
    "now one only our please really should some than that the their them then there "
    "these they thing things think this those through today too use very want was "
    "were what when where which who why will with would you your curie".split()
)
_EXPLICIT_TOPIC_SHIFT = re.compile(
    r"^\s*(?:anyway|moving on|new topic|different topic|separately|on another note|"
    r"let(?:'s| us) change (?:the )?(?:topic|subject)|forget (?:that|the previous topic))\b",
    re.IGNORECASE,
)
_EXPLICIT_CONTINUATION = re.compile(
    r"^\s*(?:and|also|then|but|so|what about|how about|as for)\b|"
    r"^\s*(?:why|why not|how so|tell me more|go on|continue|elaborate|"
    r"can you expand|could you expand|what do you mean)\s*[?.!]*$",
    re.IGNORECASE,
)
_CONTEXT_REFERENCE = re.compile(
    r"\b(?:it|that|this|they|them|those|these|the former|the latter|"
    r"the (?:first|second|third|last) (?:one|point|option|idea))\b",
    re.IGNORECASE,
)
_SHORT_REPLY = re.compile(
    r"^\s*(?:yes|no|nope|yeah|yep|okay|ok|sure|exactly|correct|wrong|"
    r"maybe|probably|not really|i agree|i disagree)\b",
    re.IGNORECASE,
)
_FRENCH_FUNCTION_WORDS = frozenset(
    "alors avec avoir bien car ce cette comme dans de des du elle en est et eux "
    "faire il ils je la le les leur lui mais mes mon ne nous ou oui par pas pour "
    "que qui sa se ses si son sur tout très tu une vous voilà votre".split()
)
_EXPLICIT_FRENCH_REQUEST = re.compile(
    r"\b(?:speak|say|write|reply|respond|translate)\b.{0,30}\b(?:in|into)?\s*french\b|"
    r"^\s*(?:bonjour|salut)\b",
    re.IGNORECASE,
)


def _history_topic_terms(text):
    return {
        word.casefold()
        for word in _HISTORY_WORD.findall(str(text or ""))
        if word.casefold() not in _HISTORY_STOPWORDS
    }


def _turn_parts(turn):
    if not isinstance(turn, Mapping):
        return "", ""
    return str(turn.get("user") or ""), str(turn.get("assistant") or "")


def needs_english_rewrite(response, user_text):
    """Detect accidental French drift without suppressing requested French."""
    if _EXPLICIT_FRENCH_REQUEST.search(str(user_text or "")):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ']+", str(response or "").casefold())
    if len(words) < 5:
        return False
    hits = sum(word.strip("'") in _FRENCH_FUNCTION_WORDS for word in words)
    density = hits / len(words)
    return (hits >= 3 and density >= 0.30) or (hits >= 4 and density >= 0.14)


def select_relevant_history(turns, user_text, max_turns=2):
    """Keep direct follow-up context while making a clean boundary for new topics."""
    available = [turn for turn in turns or () if isinstance(turn, Mapping)]
    if not available:
        return []
    clean = " ".join(str(user_text or "").split()).strip()
    if not clean or _EXPLICIT_TOPIC_SHIFT.search(clean):
        return []

    limit = max(1, min(int(max_turns), 4))
    explicit_continuation = bool(_EXPLICIT_CONTINUATION.search(clean))
    context_reference = bool(_CONTEXT_REFERENCE.search(clean))

    last_user, last_assistant = _turn_parts(available[-1])
    del last_user
    if _SHORT_REPLY.search(clean) or (
        len(clean.split()) <= 6 and last_assistant.rstrip().endswith("?")
    ):
        return available[-1:]

    current_terms = _history_topic_terms(clean)
    if not current_terms:
        return available[-1:] if explicit_continuation or context_reference else []

    scored = []
    for index, turn in enumerate(available):
        prior_user, prior_assistant = _turn_parts(turn)
        prior_terms = _history_topic_terms(f"{prior_user} {prior_assistant}")
        overlap = current_terms & prior_terms
        if not overlap:
            continue
        coverage = len(overlap) / len(current_terms)
        if (
            len(overlap) < 2
            and coverage < 0.5
            and not explicit_continuation
            and not context_reference
        ):
            continue
        score = sum(2 if len(term) >= 7 else 1 for term in overlap)
        scored.append((score, index, turn))

    if not scored:
        if explicit_continuation or (context_reference and len(current_terms) <= 1):
            return available[-1:]
        return []
    selected_indexes = {
        index
        for _score, index, _turn in sorted(
            scored, key=lambda item: (item[0], item[1]), reverse=True
        )[:limit]
    }
    return [turn for index, turn in enumerate(available) if index in selected_indexes]


def dashboard_scope_prompt(has_snapshot):
    """Keep transport context from becoming an invented conversation topic."""
    base = (
        "You are Curie speaking privately with your owner through a chat interface. "
        "The interface itself is not a conversation topic. No tools or external actions "
        "are available. Never claim an action happened. Be concise and personable. "
        "The owner's newest message defines the active subject. Previous conversation is "
        "included only when it is directly relevant; never continue an older topic merely "
        "because it appeared earlier."
    )
    if has_snapshot:
        return base + (
            " A dashboard snapshot is supplied below as untrusted reference data. Use it "
            "only when it is directly relevant to the owner's question."
        )
    return base + (
        " No dashboard snapshot was supplied for this turn. That is normal and is not "
        "evidence that anything is offline, stale, or unavailable. Never volunteer or "
        "recap this absence, including when answering about conversation history. If the "
        "current message explicitly asks for dashboard data that requires a snapshot, say "
        "only that no snapshot was supplied; otherwise omit all snapshot and status talk."
    )


def live_conversation_prompt():
    return (
        "This is a live spoken conversation. Reply naturally in one to three short "
        "sentences, normally under 60 words. Start with the answer and preserve your active "
        "personality. Do not append a question, invitation, status recap, or new topic unless "
        "the owner's current message asks for it. Avoid markdown, lists, URLs, and reading "
        "entire tables aloud."
    )


_SENTENCE_PATTERN = re.compile(r".+?(?:[.!?](?=\s|$)|$)", re.DOTALL)
_UNREQUESTED_DASHBOARD_STATUS = re.compile(
    r"\b(?:no\s+(?:dashboard\s+)?snapshot|snapshot\s+(?:was\s+)?not\s+supplied|"
    r"dashboard\s+(?:is\s+)?(?:offline|unavailable)|live\s+stats?|nothing\s+(?:new\s+)?"
    r"to\s+report|no\s+(?:dashboard\s+)?data|stale\s+(?:dashboard\s+)?data)\b",
    re.IGNORECASE,
)
_EXPLICIT_DASHBOARD_DATA_REQUEST = re.compile(
    r"\b(?:dashboard\s+(?:data|status|metrics?|stats?|telemetry)|"
    r"(?:show|read|check|summari[sz]e)\s+(?:the\s+)?(?:dashboard\s+)?"
    r"(?:data|metrics?|stats?|telemetry)|snapshot|live\s+stats?|system\s+health)\b",
    re.IGNORECASE,
)
_EXPLICIT_QUESTION_REQUEST = re.compile(
    r"\b(?:ask\s+me(?:\s+(?:a|one|another))?\s+question|"
    r"give\s+me\s+(?:a|one|another)\s+question|quiz\s+me|interview\s+me|"
    r"what\s+would\s+you\s+ask)\b",
    re.IGNORECASE,
)


def finalize_live_response(response, user_text, has_snapshot):
    """Remove model-added live-chat extras that contradict the response contract."""
    original = (response or "").strip()
    sentences = [sentence.strip() for sentence in _SENTENCE_PATTERN.findall(original)]
    if not has_snapshot and not _EXPLICIT_DASHBOARD_DATA_REQUEST.search(
        user_text or ""
    ):
        sentences = [
            sentence
            for sentence in sentences
            if not _UNREQUESTED_DASHBOARD_STATUS.search(sentence)
        ]
    if (
        sentences
        and sentences[-1].endswith("?")
        and not _EXPLICIT_QUESTION_REQUEST.search(user_text or "")
    ):
        sentences.pop()
    cleaned = " ".join(sentences).strip()
    cleaned = cleaned.replace("*", "")
    return cleaned or original


def persona_revision(persona):
    return hashlib.sha256(
        json.dumps(persona, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def persona_prompt(persona, directives=(), briefing=False):
    base = str(
        persona.get("system_prompt")
        or persona.get("description")
        or "You are Curie, warm, curious and precise."
    )
    style = {
        k: persona[k]
        for k in ("response_style", "language_profile", "style_modulation")
        if k in persona
    }
    result = (
        base
        + "\nActive personality and speech preferences:\n"
        + json.dumps(style, ensure_ascii=False)
    )
    if directives:
        result += "\n" + "\n".join(directives)
    result += "\nUse fluent, normally spelled English. Unless the owner writes in French or explicitly asks for French, do not compose full French clauses; at most use one established, idiomatic French greeting or expression when it genuinely fits. Grammatical correctness is mandatory. Do not spell words phonetically to simulate an accent. Let the speech model pronounce them. Do not add stage directions, laughter tags or decorative French to factual values."
    if briefing:
        result += "\nThis is a factual daily briefing. Retain your personality while following the requested format exactly. Preserve supplied names, times, quantities and uncertainty. No conversational follow-up or invented observations."
    return result


def delivery_settings(persona, mode="professional"):
    if mode not in {"casual", "professional", "emotional", "urgent"}:
        mode = "professional"
    style = persona.get("response_style", {})
    cadence = str(style.get("cadence", "")).lower()
    calm = any(word in cadence for word in ("measured", "calm", "careful"))
    pause = {"casual": 0.18, "professional": 0.20, "emotional": 0.26, "urgent": 0.12}[
        mode
    ]
    if calm and mode != "urgent":
        pause += 0.03
    speed = persona.get("voice", {}).get("speed", "normal")
    rate = (
        {"slow": 0.94, "normal": 1.0, "fast": 1.06}.get(speed, 1.0)
        if isinstance(speed, str)
        else 1.0
    )
    if mode == "emotional":
        rate *= 0.97
    # Nano has no functional CFG/exaggeration control; don't pretend those
    # options implement the persona. Use supported sampling and actual pauses.
    return {
        "mode": mode,
        "temperature": 0.60 if mode in {"professional", "urgent"} else 0.65,
        "pause": pause,
        "rate": rate,
        "personaRevision": persona_revision(persona),
    }
