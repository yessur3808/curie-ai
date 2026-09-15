"""Deterministic conversation planning for warmth without performative empathy."""

from __future__ import annotations

import re
from typing import Any

from agent.kernel.contracts import ResponseMode

_SIGNALS = {
    "urgency": re.compile(r"\b(?:urgent|emergency|asap|right now|immediately)\b", re.I),
    "fatigue": re.compile(
        r"\b(?:exhausted|worn out|so tired|no energy|burnt? out)\b", re.I
    ),
    "frustration": re.compile(
        r"\b(?:frustrated|annoyed|fed up|keeps failing|still broken)\b", re.I
    ),
    "celebration": re.compile(
        r"\b(?:i did it|we did it|passed|got the job|won|finally works)\b", re.I
    ),
    "loneliness": re.compile(
        r"\b(?:lonely|alone|no one to talk to|miss having someone)\b", re.I
    ),
}

_SOCIAL = re.compile(
    r"^(?:(?:hi+|hello+|hey+|bonjour|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+curie)?[,!. ]*)?(?:how\s+are\s+you(?:\s+doing)?|"
    r"how(?:'s|\s+is)\s+it\s+going)[?!. ]*$|"
    r"^(?:hi+|hello+|hey+|bonjour)(?:\s+curie)?[?!. ]*$",
    re.I,
)
_CASUAL_QUIP = re.compile(
    r"^(?:that was|well,?\s+that was|not bad|nice one|well played|look at you)"
    r"\b.{0,100}[.!?]*$",
    re.I | re.S,
)
_COMMAND = re.compile(
    r"^(?:(?:please\s+)|(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?))?"
    r"(?:turn|switch|set|start|stop|open|close|lock|unlock|"
    r"enable|disable|run|send|show|check|cancel|pause|resume|remind|schedule)\b",
    re.I,
)
_EXPLICIT_DEPTH = re.compile(
    r"\b(?:in depth|deep dive|detailed|thorough|comprehensive|step[- ]by[- ]step|"
    r"explain fully|full analysis|all the details|from first principles)\b",
    re.I,
)
_FOCUSED = re.compile(
    r"\b(?:why|how|compare|analy[sz]e|research|plan|design|implement|debug|fix|"
    r"review|architecture|tradeoffs?|recommend|explain|brainstorm|evaluate|investigate|"
    r"build|create|draft|write|summari[sz]e)\b",
    re.I,
)
_TECHNICAL = re.compile(
    r"\b(?:code|python|bug|error|stack|api|database|algorithm|optimi[sz]e|"
    r"debug|architecture|model)\b",
    re.I,
)
_FORMAL = re.compile(
    r"\b(?:formal|professional tone|business tone|official wording|executive summary|"
    r"cover letter|legal memo|formal report)\b",
    re.I,
)


def _interaction_kind(text: str) -> str:
    clean = text.strip()
    if _SOCIAL.fullmatch(clean) or _CASUAL_QUIP.fullmatch(clean):
        return "social"
    if _COMMAND.search(clean):
        return "command"
    if _FOCUSED.search(clean):
        return "explanation"
    return "conversation"


def _length_target(text: str, verbosity: str, *, urgent: bool, interaction: str) -> str:
    if urgent or verbosity == "concise" or interaction in {"social", "command"}:
        return "brief"
    if _EXPLICIT_DEPTH.search(text):
        return "deep"
    if _FOCUSED.search(text) or len(text.split()) > 28:
        return "deep" if verbosity == "detailed" else "focused"
    if verbosity == "detailed":
        return "focused"
    return "brief"


def select_response_mode(
    text: str,
    *,
    routing_intent: str | None = None,
    capability: str | None = None,
    preferences: dict | None = None,
) -> ResponseMode:
    """Choose an explicit response contract independently of generation style."""
    if routing_intent in {"clarification", "multiple_intents"}:
        return ResponseMode.CLARIFICATION
    if capability in {"home_status", "ram_usage", "hardware", "network_speed"}:
        return ResponseMode.STATUS
    if routing_intent in {"system_command", "approval"} or capability:
        return ResponseMode.COMMAND_ACK
    interaction = _interaction_kind(text)
    if interaction == "social":
        return ResponseMode.SOCIAL
    length = _length_target(
        text,
        str((preferences or {}).get("verbosity", "balanced")),
        urgent=bool(_SIGNALS["urgency"].search(text)),
        interaction=interaction,
    )
    return {
        "brief": ResponseMode.BRIEF,
        "focused": ResponseMode.FOCUSED,
        "deep": ResponseMode.DEEP,
    }[length]


def plan_response(text: str, preferences: dict | None = None) -> dict[str, Any]:
    """Choose independent answer, care, action, length, and expression policies."""
    preferences = preferences or {}
    emotion = next(
        (name for name, pattern in _SIGNALS.items() if pattern.search(text)), "neutral"
    )
    urgent = emotion == "urgency"
    interaction = _interaction_kind(text)
    acknowledgement = {
        "fatigue": "acknowledge_once_then_reduce_load",
        "frustration": "acknowledge_once_then_fix",
        "celebration": "celebrate_briefly_and_specifically",
        "loneliness": "acknowledge_once_without_dependency_cues",
        "urgency": "skip_social_padding",
    }.get(
        emotion,
        (
            "result_or_blocker_only"
            if interaction == "command"
            else "only_if_naturally_relevant"
        ),
    )
    verbosity = preferences.get("verbosity", "balanced")
    length = _length_target(text, verbosity, urgent=urgent, interaction=interaction)
    affection = preferences.get("affection", "gentle")
    french = preferences.get("french_frequency", "natural")
    formal = bool(_FORMAL.search(text))
    mode = select_response_mode(text, preferences=preferences)
    if urgent or interaction == "command" or formal or _TECHNICAL.search(text):
        french = "none"
    return {
        "mode": mode.value,
        "emotion": emotion,
        "interaction": interaction,
        "answer": "direct_first",
        "directness": "direct_first",
        "acknowledgement": acknowledgement,
        "next_action": (
            "report_verified_result_or_blocker"
            if interaction == "command"
            else "one_practical_step_if_useful"
        ),
        "length": length,
        "personality": (
            "calm"
            if urgent
            else ("composed_and_natural" if formal else "warm_and_casual")
        ),
        "affection": (
            "none" if urgent or interaction == "command" or formal else affection
        ),
        "french": french,
        "french_usage": french,
        "end_with_question": False,
        "layout": preferences.get("response_layout", "adaptive"),
    }


def planner_directives(plan: dict[str, Any]) -> list[str]:
    directives = [
        f"- Response plan: mode={plan.get('mode', 'brief')}, interaction={plan['interaction']}, answer={plan['answer']}, "
        f"acknowledgement={plan['acknowledgement']}, "
        f"next_action={plan['next_action']}, length={plan['length']}.",
        f"- Relational expression: personality={plan['personality']}, affection={plan['affection']}, "
        f"French={plan['french']}. Do not end with a question by default.",
        "- Use one sincere acknowledgement at most. Never imply need, exclusivity, jealousy, guilt, "
        "human feelings, or that the user should withdraw from other people.",
    ]
    if plan.get("interaction") == "command":
        directives.append(
            "- Command style: lead with the verified result or the blocker. Keep it to one "
            "or two crisp sentences unless safety or recovery steps require more. Do not begin "
            "with 'Certainly', 'Absolutely', 'As requested', or a recap of the command."
        )
    if plan.get("layout") == "structured":
        directives.append(
            "- Presentation preference: keep responses clean and scannable. Use concise bullets "
            "or a small Markdown table when they materially clarify multi-part information."
        )
    return directives
