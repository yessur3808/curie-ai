"""Small, deterministic boundaries for acknowledgements and casual updates.

These are presentation rules, not a general fact checker. Never classify an
open-ended message as a closed acknowledgement just because it starts with thanks.
"""

import re


def weather_observation(text: str) -> bool:
    """Closed vocabulary for a harmless weather correction, never a request.

    This deliberately abstains on unfamiliar wording. A broad weather keyword
    match must not consume an additional instruction or a personal disclosure.
    """
    words = re.findall(r"[\w'’]+", text.casefold())
    allowed = {
        "no",
        "rain",
        "storm",
        "snow",
        "not",
        "raining",
        "rainy",
        "snowing",
        "it",
        "is",
        "it's",
        "it’s",
        "quite",
        "very",
        "actually",
        "hot",
        "warm",
        "humid",
        "muggy",
        "stuffy",
        "here",
        "but",
        "at",
        "least",
        "there",
        "and",
        "still",
        "today",
        "now",
        "just",
        "outside",
        "i'm",
        "i’m",
        "i",
        "am",
        "surviving",
    }
    return bool(
        words
        and set(words) <= allowed
        and not any(mark in text for mark in ("?", '"', "`", "“", "”"))
        and re.search(
            r"\b(?:no (?:rain|storm|snow)|not (?:raining|rainy|snowing))\b", text, re.I
        )
        and not re.search(r"\bnot (?:hot|warm|humid|muggy|stuffy)\b", text, re.I)
    )


def without_weather_preface(text: str) -> str:
    """Remove at most one closed weather statement before a separate request."""
    preface = re.fullmatch(r"([^.!?;]+)[.!;]\s+(.+)", text.strip(), re.S)
    if preface and weather_observation(preface.group(1)):
        return preface.group(2)
    return text


ACKNOWLEDGEMENT = re.compile(
    r"^(?:(?:thanks(?: a lot)?|thank you|cheers)"
    r"(?:[,!. ]+(?:that (?:helped|helps|worked)|that(?:['’]s| is) helpful|"
    r"(?:that )?makes sense|much appreciated|for (?:the|that) "
    r"(?:(?:weather|news|engineering) )?(?:update|brief|explanation|help)))?|"
    r"got it|okay|ok|cool|perfect|sounds good|all right|alright)[.! ]*$",
    re.I,
)

_BARE_UPDATE = re.compile(
    r"^(?:english[ -]only:\s*)?i (?:(?:prefer|like) |"
    r"found (?:the |my |a )?(?:missing )?(?:puzzle piece|piece|keys?|book)\b)"
    r"[\w ,’'\-]{0,100}[.!]?$",
    re.I,
)
_NEEDS_CONVERSATION = re.compile(
    r"\b(?:ask|question|chat|discuss|help|advice|what|why|how|should|could|"
    r"but|please|need|want|worried|scared|pain|hurt|blood|weapon|gun|"
    r"pill|pills|drug|drugs|suicide|suicidal|dead|dying|danger|unsafe|"
    r"medical|medicine|legal|financial|investment|error|bug|security|code|api|"
    r"password|secret|token|emergency)\b",
    re.I,
)
_GENERIC_NEXT = re.compile(
    r"(?:what['’]s next(?: on your mind)?|what is next(?: on your mind)?|"
    r"what else(?: is on your mind)?|anything else(?: on your mind)?)\?",
    re.I,
)
_REPETITION_FEEDBACK = re.compile(
    r"\byou(?:['’]ve| have)? (?:already|just) (?:shared|sent|told|posted|mentioned)\b|"
    r"\b(?:stop|keep) repeating\b|"
    r"\b(?:brief|briefing|update|reply|answer|news)\b[^.!?]{0,40}\b(?:repetitive|repeated|same again)\b",
    re.I,
)


def repetition_feedback(text: str) -> bool:
    """Identify explicit feedback about repetition, not a request for another brief."""
    return bool(_REPETITION_FEEDBACK.search(text))


def closed_repetition_feedback(text: str) -> bool:
    """A standalone repetition complaint can be acknowledged without inventing news.

    Never consume a question, request for explanation, or an additional action.
    Open-ended feedback still needs the model and conversation context.
    """
    if "?" in text or re.search(
        r"\b(?:but|why|what|how|when|where|which|please|can|could|would|want|need|"
        r"remind|schedule|send|check|show|tell|find|explain|give|fetch|instead|"
        r"and|then|so|stop|never|avoid|turn|switch|run|delete|open|close|make)\b",
        text,
        re.I,
    ):
        return False
    return bool(
        re.fullmatch(
            r"\s*(?:(?:hey|hi)[, ]+(?:curie[, ]+)?)?"
            r"you(?:['’]ve| have)? already (?:shared|sent|posted|mentioned|told me about)\s+"
            r"[^.!?;\n]{1,120}[.! ]*",
            text,
            re.I,
        )
    )


def bare_casual_update(text: str) -> bool:
    return bool(_BARE_UPDATE.fullmatch(text.strip())) and not bool(
        _NEEDS_CONVERSATION.search(text)
    )


def trim_idle_question(response: str, user_text: str) -> str:
    """Drop only a trailing idle question after a substantive prose answer.

    Keep sole questions, explicit invitations, safety/clarification requests, code,
    quotes and multiline answers intact. Specific useful follow-ups elsewhere survive.
    """
    if any(mark in response for mark in ("\n", "`", '"', "“", "”")):
        return response
    match = re.search(r"(?<=[.!])\s+([^.!?]+\?)\s*$", response)
    if not match:
        return response
    question = match.group(1).strip()
    if _GENERIC_NEXT.fullmatch(question):
        return response[: match.start()].rstrip()
    return response
