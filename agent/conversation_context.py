"""Shared, conservative conversation/reference signals, not canned answers."""

import re
from collections.abc import Mapping

_REPORT = re.compile(
    r"^\s*(?:you\s+(?:previously\s+|just\s+|earlier\s+)?"
    r"(?:said|mentioned|reported|told me)|"
    r"(?:i(?:'m| am| was)\s+)?(?:asking|verifying|referring to)\s+(?:about\s+)?"
    r"(?:the|that|your)\s+(?:error|failure|reply|response))\b",
    re.I,
)
_EXPLANATION = re.compile(
    r"^\s*(?:what\s+(?:is|was|caused)\s+(?:the|that|this)\s+"
    r"(?:error|failure|problem)|why\s+(?:did\s+)?(?:it|that|this)\s+"
    r"(?:fail|stop|happen)|what\s+(?:went wrong|do you mean)|how so|why not)\b",
    re.I,
)
_SHIFT = re.compile(
    r"^\s*(?:anyway|new topic|different topic|moving on|separately|"
    r"on another note|forget (?:that|it))\b",
    re.I,
)
_REFERENCE = re.compile(
    r"\b(?:it|that|them|those|these|there|instead|alternative|the former|the latter|"
    r"(?:first|second|third|last) (?:one|option)|you (?:said|mentioned))\b",
    re.I,
)
_ELLIPSIS = re.compile(
    r"^\s*(?:why\??|how so\??|tell me more|go on|continue|"
    r"(?:make|keep) it\b.*|(?:too|less|more) (?:long|formal|short|casual)|"
    r"what about\b.*|and\b.*|but\b.*|then\b.*)\s*[.!?]*$",
    re.I,
)


def discussion_only(text):
    """Discussion, explanation and prohibitions do not authorize execution."""
    clean = str(text or "").strip()
    if clean.startswith("/"):
        return False
    if re.match(
        r"(?:please\s+)?(?:call|name|rename)\s+(?:(?:the|a|an|my|our|this)\s+)?"
        r"(?:fictional|imaginary|pretend)\b",
        clean,
        re.I,
    ):
        # Naming an explicitly fictional object is not a device-alias command.
        return True
    if re.match(r"(?:back|returning)\s+to\b", clean, re.I) and re.search(
        r"\bremind me (?:of|about)\b", clean, re.I
    ):
        from agent.skills.scheduler import _parse_due_time

        # Conversational recall is not permission to schedule a notification.
        # Keep explicit due-time requests available to the normal scheduler.
        if _parse_due_time(clean) is None:
            return True
    # A prohibited or hypothetical action is not a request to execute it.
    # Abstain on mixed directives too: keyword routing cannot safely determine
    # which clause owns an action. Do not match embedded reminder content.
    if re.search(
        r"^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?"
        r"(?:please\s+)?(?:don['’]t|do not|not|never|avoid|refrain from)\s+\S",
        clean,
        re.I,
    ):
        return True
    if re.search(
        r"^\s*(?:please\s+)?i(?:\s+(?:don['’]t|do not)\s+want\s+you\s+to|"
        r"(?:\s+would|['’]d)\s+rather\s+you\s+(?:didn['’]t|did not))\s+\S",
        clean,
        re.I,
    ):
        return True
    if re.search(
        r"^\s*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
        r"(?:how\s+(?:would|could|do|can)\s+you|"
        r"(?:explain|describe|tell me|show me)\s+how\s+"
        r"(?:you(?:\s+(?:would|could)|['’]d)?|to))\b",
        clean,
        re.I,
    ):
        return True
    if re.search(
        r"\bwithout\s+(?:(?:actually|really)\s+)?"
        r"(?:accessing|checking|executing|running|calling|"
        r"using\s+(?:(?:any|the|your)\s+)?(?:tools?|apis?))\b",
        clean,
        re.I,
    ):
        return True
    reported_status = re.search(
        r"\b(?:returned|received|gave|got|reported)\s+(?:an?\s+)?(?:http\s+)?[45]\d\d\b",
        clean,
        re.I,
    )
    explains_status = re.search(
        r"\b(?:what does (?:that|this|it) mean|what (?:is|was|caused) (?:the|that) error|"
        r"(?:don['’]t|do not) (?:retry|rerun|execute))\b",
        clean,
        re.I,
    )
    if reported_status and explains_status:
        return True
    if re.search(
        r"\bwhy\s+(?:would|did|do)\s+you\s+(?:offer|suggest|check|mention|ask about)\b",
        clean,
        re.I,
    ):
        return True
    return bool(_REPORT.search(clean) or _EXPLANATION.search(clean))


def history_text(history):
    parts = []
    for item in history or ():
        if isinstance(item, Mapping):
            parts.extend(
                str(item.get(k) or "") for k in ("content", "user", "assistant")
            )
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            parts.append(str(item[1]))
    return " ".join(parts)


def context_followup(text, history=()):
    clean = str(text or "").strip()
    if not history or _SHIFT.search(clean) or clean.startswith("/"):
        return False
    if discussion_only(clean) or _ELLIPSIS.fullmatch(clean):
        return True
    if _REFERENCE.search(clean):
        return True
    # Reusing a specific topic noun in a question is evidence of continuity.
    # Do not treat general words (e.g. 'what', 'can', 'please') as topic evidence.
    terms = set(re.findall(r"\b[a-z]{4,}\b", clean.casefold())) - {
        "what",
        "when",
        "where",
        "which",
        "would",
        "could",
        "should",
        "please",
        "have",
        "with",
        "about",
        "your",
        "that",
        "this",
        "there",
        "some",
        "really",
        "just",
        "today",
        "does",
        "will",
        "from",
        "know",
        "like",
    }
    prior = set(
        re.findall(r"\b[a-z]{4,}\b", history_text(list(history)[-4:]).casefold())
    )
    return bool(terms & prior) and (clean.endswith("?") or len(clean.split()) <= 12)
