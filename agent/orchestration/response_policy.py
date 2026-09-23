"""One sanitation and personality boundary for every response source."""

from __future__ import annotations

import re

SPEAKER_TAG_PATTERN = re.compile(
    r"^\s*(?:User:|Curie:|Assistant:|Coder:|System:)", re.I | re.M
)
META_NOTE_PATTERN = re.compile(r"\[(?:Note|Meta|Aside|System):[^\]]*\]", re.I)
ACTION_PATTERN = re.compile(
    r"\*(?:(?:I\s+)?(?:smiles?|gestures?|nods?|laughs?|sighs?|shrugs?|waves?|"
    r"blinks?|pauses?|leans?|offers?|gives?|pours?|sits?|stands?))[^*\n]*\*",
    re.I,
)
THINK_PATTERN = re.compile(r"<think>[\s\S]*?</think>|<think>[\s\S]*$", re.I)
CANNED_FRENCH_SUFFIX_PATTERN = re.compile(
    r"(?:,\s*|\s+)\*?(?:c['’]est\s+(?:dommage|magnifique)|très\s+bien|"
    r"ça\s+va|voilà|merci|d['’]accord|bien\s+sûr)\*?"
    r"(?:,?\s*(?:oui|non))?[?!.]*\s*$",
    re.I,
)
CANNED_FRENCH_ASIDE_PATTERN = re.compile(
    r"\s*\*(?:oui|non|bonjour|merci|voilà|d['’]accord|bien\s+sûr)\b[^*\n]{0,80}\*\s*$",
    re.I,
)
CANNED_ADDRESS_SUFFIX_PATTERN = re.compile(
    r"(?:,\s*|\s+)(?:monsieur|madame|mon ami)[?!.]*\s*$", re.I
)
CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```|```[\s\S]*$", re.M)
INLINE_CODE_PATTERN = re.compile(r"`[^`]+`")
DEPENDENCY_PATTERN = re.compile(
    r"\b(?:I need you|you only need me|I(?:'ll| will) always be all you need|"
    r"don't leave me|never leave me|I get jealous|choose me over)\b",
    re.I,
)


def _remove_suffix_preserving_punctuation(text: str, pattern: re.Pattern) -> str:
    match = pattern.search(text)
    if not match:
        return text
    suffix = match.group(0)
    punctuation = next((mark for mark in "?!" if mark in suffix), "")
    if not punctuation and "." in suffix:
        punctuation = "."
    stem = text[: match.start()].rstrip()
    if punctuation and stem and stem[-1] not in ".!?":
        stem += punctuation
    return stem


def naturalize_prose_punctuation(text: str) -> str:
    protected = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
    parts: list[str] = []
    cursor = 0
    for match in protected.finditer(text):
        parts.append(_rewrite_punctuation(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_rewrite_punctuation(text[cursor:]))
    return "".join(parts)


def _rewrite_punctuation(text: str) -> str:
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r";\s+([A-Z])", lambda match: ". " + match.group(1), text)
    return re.sub(r";\s*", ", ", text)


class ResponsePolicy:
    def __init__(
        self, persona: dict, personality_context, minimal_sanitization: bool = True
    ):
        self.persona = persona
        self.personality_context = personality_context
        self.minimal_sanitization = minimal_sanitization

    def sanitize(self, response: str) -> str:
        response = SPEAKER_TAG_PATTERN.sub("", response).strip()
        response = THINK_PATTERN.sub("", response).strip()
        response = META_NOTE_PATTERN.sub("", response).strip()
        response = ACTION_PATTERN.sub("", response).strip()
        response = DEPENDENCY_PATTERN.sub("I’m here to help", response).strip()
        if (self.persona.get("name") or "").strip().lower() == "curie":
            response = _remove_suffix_preserving_punctuation(
                response, CANNED_FRENCH_SUFFIX_PATTERN
            )
            response = CANNED_FRENCH_ASIDE_PATTERN.sub("", response).rstrip()
            # A French address can be charming when context earns it.  Appending
            # one to every answer is mechanical and makes command replies sound
            # formal, so the shared boundary removes only canned end tags.
            response = _remove_suffix_preserving_punctuation(
                response, CANNED_ADDRESS_SUFFIX_PATTERN
            )
            response = naturalize_prose_punctuation(response)
        if not self.minimal_sanitization:
            response = CODE_BLOCK_PATTERN.sub("", response).strip()
            response = INLINE_CODE_PATTERN.sub("", response).strip()
        response = re.sub(r" +", " ", response)
        response = re.sub(r"\n\n\n+", "\n\n", response)
        return response.strip()

    def finalize(
        self, response: str, user_text: str, profile=None, history=None
    ) -> str:
        sanitized = self.sanitize(response)
        styled = self.personality_context.apply_response_style(
            sanitized, user_text, user_profile=profile, history=history
        )
        if re.search(
            r"\b(?:keep\s+it\s+brief|be\s+brief|brief\s+answer|no\s+follow[- ]?up)\b",
            str(user_text or ""),
            re.I,
        ):
            # Remove only an appended question sentence.  The earlier pattern
            # could begin at any matching auxiliary verb, so a response such
            # as ``Here are three ideas: ... Which one?`` was reduced to
            # ``Here`` because ``are`` matched near the start of the answer.
            styled = re.sub(
                r"(?:\n+|(?<=[.!])\s+)(?:which|would|do|does|is|are|what|how|can|could|should)\b"
                r"[^?\n]*\?\s*$",
                "",
                styled,
                flags=re.I,
            ).rstrip()
        return styled
