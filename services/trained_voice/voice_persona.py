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
    r"can you expand|could you expand|what do you mean|suppose i insist|"
    r"even if i insist|(?:that was .+?[.!]\s*)?try again(?:,? like .+)?|"
    r"(?:give me )?(?:the )?"
    r"(?:short|shorter|brief) version(?: of that)?)\s*[?.!]*$",
    re.IGNORECASE,
)
_IMMEDIATE_CONTINUATION = re.compile(
    r"^\s*(?:suppose i insist|even if i insist|"
    r"suppose i authorize it|(?:yes|okay|fine)[,.]?\s*(?:authorize|approved)|"
    r"suppose i (?:say|claim|argue)\b.+|"
    r"what would you need from me first|what rule would you use|"
    r"pick the first diagnostic\b.+|give me the call\b.+|"
    r"design the next safe test\b.+|give me your best opinion\b.+|"
    r"(?:do not|don['’]t) give me a roadmap\b.+|"
    r"pick one|fix only that one|why (?:that|this|those)\b.+|"
    r"does that change (?:your|the) .+|what exactly are you continuing from|"
    r"do it hypothetically|make it sound like me\b.+|"
    r"the (?:first|second|third|last) one\b.+|"
    r"(?:that was .+?[.!]\s*)?try again(?:,? like .+)?|"
    r"fine[,.]?\s+(?:give|show|tell)\b.+|"
    r"(?:give me )?(?:the )?(?:short|shorter|brief) version(?: of that)?)\s*[?.!]*$",
    re.I,
)
_ELLIPTICAL_FOLLOWUP = re.compile(
    r"^\s*(?:and|but|so|then|now)\b|"
    r"^\s*(?:could it still|what if|and if|and what|before any push|continue|"
    r"too (?:formal|polished|long|wordy|cautious)|"
    r"what can you (?:actually|genuinely) (?:do|prepare)|"
    r"what would you need|which voice should you use)\b",
    re.I,
)
_CONTEXT_REFERENCE = re.compile(
    r"\b(?:it|that|this|they|them|those|these|there|instead|alternative|the former|the latter|"
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
    r"\b(?:speak|say|write|reply|respond|translate)\b.{0,30}\b(?:in|into)?\s*french\b",
    re.IGNORECASE,
)

_REPEAT_REQUEST = re.compile(
    r"\b(?:repeat|say that again|same answer|verbatim|quote yourself)\b", re.I
)
_MEMORY_ARCHITECTURE = re.compile(
    r"\b(?:your|assistant|curie(?:'s)?)\s+(?:memory|context)\s+(?:system|architecture)\b|"
    r"\b(?:redesign|architect|architecture)\b.{0,40}\b(?:memory|retention)\b",
    re.I,
)
_PRIORITY_QUESTION = re.compile(
    r"\b(?:what|which)\b.{0,35}\b(?:first|priority|attention)\b|"
    r"\bwhat matters first\b",
    re.I,
)
_CANDID_RETRY = re.compile(
    r"\b(?:too diplomatic|that was diplomatic|be honest|be blunt|"
    r"try again,? like (?:a )?(?:good|real) friend)\b",
    re.I,
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
    if re.search(
        r"[\u3400-\u4dbf\u4e00-\u9fff]", str(response or "")
    ) and not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", str(user_text or "")):
        return True
    user_words = re.findall(r"[A-Za-zÀ-ÿ']+", str(user_text or "").casefold())
    user_french_hits = sum(
        word.strip("'") in _FRENCH_FUNCTION_WORDS for word in user_words
    )
    if _EXPLICIT_FRENCH_REQUEST.search(str(user_text or "")) or (
        user_french_hits >= 2 and user_french_hits / max(len(user_words), 1) >= 0.25
    ):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ']+", str(response or "").casefold())
    if len(words) < 5:
        return False
    hits = sum(word.strip("'") in _FRENCH_FUNCTION_WORDS for word in words)
    density = hits / len(words)
    return (hits >= 3 and density >= 0.30) or (hits >= 4 and density >= 0.14)


def select_relevant_history(turns, user_text, max_turns=4):
    """Keep a chronological recent window, bounded by explicit topic changes.

    Lexical ranking must not replace the immediate exchange with an older one:
    incidental words such as "I'm" previously resurrected unrelated drafts.
    Recent transcript is evidence, not an instruction to continue its subject.
    """
    available = [turn for turn in turns or () if isinstance(turn, Mapping)]
    clean = " ".join(str(user_text or "").split()).strip()
    if not available or not clean or _EXPLICIT_TOPIC_SHIFT.search(clean):
        return []
    for index in range(len(available) - 1, -1, -1):
        if _EXPLICIT_TOPIC_SHIFT.search(_turn_parts(available[index])[0]):
            available = available[index:]
            break
    return available[-max(1, min(int(max_turns), 8)) :]


def dashboard_scope_prompt(has_snapshot):
    """Keep transport context from becoming an invented conversation topic."""
    base = (
        "You are Curie speaking privately with your owner through a chat interface. "
        "User-role messages are the owner's requests; assistant-role messages are your "
        "replies. Attribute earlier instructions to their actual sender, not yourself. "
        "The interface itself is not a conversation topic. No tools or external actions "
        "are available. Never claim an action happened. Be concise and personable. "
        "Answer first. Do not repeat or paraphrase the situation the owner just supplied. "
        "Honor explicit format and length constraints exactly, including a requested "
        "number of bullets or sentences. When asked about the architecture of a named "
        "subsystem, stay on that subsystem instead of substituting generic deployment patterns. "
        "Do not agree merely to be pleasant; give candid, evidence-based pushback when it "
        "helps. Do not imply personal life experience or a learning history you do not "
        "actually have. If a concrete registered capability could materially help, mention one "
        "specific next action you can take, but never append a generic offer to help. "
        "Never guess a device for a mutating command: below a unique high-confidence match, "
        "show the candidates or ask one focused clarification. "
        "A registered tool is not proof that an account is connected or that live data was "
        "checked in this turn. Distinguish configured capability, verified connection, and "
        "completed action. Never invent a location, live result, URL, device, or success. "
        "Missing connection evidence means unknown, not disconnected: do not claim an "
        "account exists, does not exist, is logged in, or is logged out without evidence. "
        "For a short draft or revision, give the draft itself. Preserve names, intent, "
        "who did what, and alternatives the owner did not ask to edit. "
        "Treat simulations and hypotheticals as discussion only. In a safety conflict, prefer "
        "the anomalous evidence, pause the risky action, and state what remains uncertain. "
        "Casual banter should be one or two short sentences, not a lecture. "
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


def turn_contract_prompt(user_text, context_turns):
    """Add small turn-specific constraints that weaker models commonly miss."""

    clean = " ".join(str(user_text or "").split()).strip()
    rules = []
    if context_turns and (
        _EXPLICIT_CONTINUATION.search(clean)
        or _ELLIPTICAL_FOLLOWUP.search(clean)
        or _SHORT_REPLY.search(clean)
        or _CONTEXT_REFERENCE.search(clean)
    ):
        rules.append(
            "This message responds only to the immediately preceding exchange. Resolve "
            "its omitted subject from that exchange and do not revive an older topic."
        )
    if _MEMORY_ARCHITECTURE.search(clean):
        rules.append(
            "The memory system means Curie's software memory: discuss retention, expiry, "
            "privacy, retrieval, and review, not human cognition or the human brain."
        )
    if _PRIORITY_QUESTION.search(clean):
        rules.append(
            "Choose one priority and give its decisive reason. Do not relist every item "
            "the owner already supplied."
        )
    if _CANDID_RETRY.search(clean):
        rules.append(
            "Give a candid position on the immediately preceding question. Do not evade, "
            "flatter, ask what is on the owner's mind, or merely offer to make changes."
        )
    if re.search(
        r"\b(?:exactly\s+)?(?:one|two|three|four|five|[1-5])\s+"
        r"(?:short\s+)?(?:bullets?|moves?|examples?|points?)\b",
        clean,
        re.I,
    ):
        rules.append(
            "Return exactly the requested number of items, one Markdown bullet per line, "
            "with no introduction or closing sentence."
        )
    if re.search(
        r"\b(?:one line|no more than \w+ lines?|no lecture|spare me)\b", clean, re.I
    ):
        rules.append(
            "Obey the requested length literally and keep only the useful answer."
        )
    if re.search(
        r"\b(?:simulation only|hypothetical(?:ly)?|do not execute|do not send)\b",
        clean,
        re.I,
    ):
        rules.append(
            "This is a simulation. Explain or draft only; do not claim that an action, "
            "lookup, message, or state change occurred."
        )
    return " Turn contract: " + " ".join(rules) if rules else ""


def handle_conversation_contract(user_text, context_turns):
    """Compatibility hook: open-ended conversation must be answered by inference.

    Scenario-specific regex replies previously bypassed the model and fabricated
    names, timings, opinions, and observations. Keep test scenarios in evaluation,
    never as an answer bank in production.
    """
    return None


def response_similarity(left, right):
    """Jaccard similarity over meaningful response words."""

    left_terms = _history_topic_terms(left)
    right_terms = _history_topic_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def max_recent_response_similarity(response, turns):
    return max(
        (
            response_similarity(response, assistant)
            for _user, assistant in map(_turn_parts, turns or ())
            if assistant
        ),
        default=0.0,
    )


def is_unhelpful_repeat(response, turns, user_text, *, threshold=0.82):
    """Flag recycled replies unless the owner explicitly requested repetition."""

    if _REPEAT_REQUEST.search(str(user_text or "")):
        return False
    return max_recent_response_similarity(response, turns) >= float(threshold)


def is_topic_regression(response, context_turns, turns, user_text):
    """Detect a follow-up that revives an older topic instead of the latest one."""

    clean = " ".join(str(user_text or "").split()).strip()
    if (
        not context_turns
        or not turns
        or not (
            _EXPLICIT_CONTINUATION.search(clean)
            or _SHORT_REPLY.search(clean)
            or _CONTEXT_REFERENCE.search(clean)
        )
    ):
        return False
    response_terms = _history_topic_terms(response)
    if len(response_terms) < 5:
        return False
    immediate_user, immediate_assistant = _turn_parts(context_turns[-1])
    immediate_terms = _history_topic_terms(f"{immediate_user} {immediate_assistant}")
    immediate_overlap = len(response_terms & immediate_terms)
    older_overlap = max(
        (
            len(
                response_terms & _history_topic_terms(f"{prior_user} {prior_assistant}")
            )
            for prior_user, prior_assistant in map(_turn_parts, list(turns)[:-1])
        ),
        default=0,
    )
    return immediate_overlap <= 1 and older_overlap >= 3


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
