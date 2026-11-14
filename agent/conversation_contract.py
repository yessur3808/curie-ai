"""Small, deterministic boundaries for acknowledgements and casual updates.

These are presentation rules, not a general fact checker. Never classify an
open-ended message as a closed acknowledgement just because it starts with thanks.
"""

import re

ACKNOWLEDGEMENT = re.compile(
    r"^(?:(?:thanks(?: a lot)?|thank you|cheers)"
    r"(?:[,!. ]+(?:that (?:helped|helps|worked)|that(?:['’]s| is) helpful|"
    r"(?:that )?makes sense|much appreciated))?|"
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
    if bare_casual_update(user_text) or (
        ACKNOWLEDGEMENT.fullmatch(user_text.strip())
        and _GENERIC_NEXT.fullmatch(question)
    ):
        return response[: match.start()].rstrip()
    return response


def trim_bare_choice(response: str, user_text: str) -> str:
    """Cap a brief everyday 'pick one' answer, without claiming to fact-check it.

    Leave requested reasons, consequential decisions and payloads alone.
    """
    if (
        len(user_text.split()) > 32
        or not re.search(r"\bpick one[.! ]*$", user_text, re.I)
        or not re.search(
            r"\b(?:tea|coffee|book|puzzle|movie|film|music|board game)\b",
            user_text,
            re.I,
        )
        or re.search(
            r"\b(?:why|explain|reason|because|compare|risk|medicine|medical|"
            r"health|legal|financial|investment|security|delete|remove|send|"
            r"deploy|code|api|database|emergency|danger|unsafe)\b",
            user_text,
            re.I,
        )
        or any(mark in response for mark in ("\n", "`", '"', "“", "”", "?"))
        or re.search(
            r"\b(?:cannot|can['’]t|unsafe|need more|must first|avoid|unless|"
            r"caution|allerg\w*|doctor|safe\w*|warning|risk\w*)\b",
            response,
            re.I,
        )
    ):
        return response
    sentences = re.split(r"(?<=[.!])\s+", response.strip())
    if len(sentences) < 2:
        return response
    # A standalone acknowledgement is not itself the requested choice.
    acknowledgement = re.fullmatch(
        r"(?:d['’]accord|voilà|bien sûr|okay|ok|all right|sure)[.!]", sentences[0], re.I
    )
    return " ".join(sentences[: 2 if acknowledgement else 1])
