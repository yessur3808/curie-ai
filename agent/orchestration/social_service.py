"""Fast deterministic handling for trivial social greetings."""

from __future__ import annotations

import re

from agent.orchestration.contracts import ResponseCandidate
from agent.conversation_contract import (
    ACKNOWLEDGEMENT,
    closed_repetition_feedback,
    weather_observation,
)


class SocialConversationService:
    _FRENCH_WELLBEING = re.compile(
        r"^\s*(?:(?:comment\s+)?(?:ça|ca)\s+va|tu\s+vas\s+bien)\s*[?!. ]*$",
        re.I,
    )
    _GREETING = re.compile(
        r"^\s*(?:hi|hello|hey|bonjour|salut)(?:\s+curie|\s+there)?"
        r"(?:[,!. ]+(?:how are you(?: doing)?|how(?:'s| is) it going))?[,!.? ]*$",
        re.I,
    )
    _PRESENCE_CHECK = re.compile(
        r"^\s*(?:hi|hello|hey|bonjour|salut)[,!. ]+"
        r"(?:are\s+)?you\s+(?:alive|awake|there)(?:\s+in\s+there)?[?!. ]*$",
        re.I,
    )
    _ACKNOWLEDGEMENT = ACKNOWLEDGEMENT
    _NO_TOOL_NEEDED = re.compile(
        r"^\s*no\s+need\s+to\s+(?:use|run|do|make)\s+(?:a|any|the)?\s*"
        r"(?:query|search|lookup|tool)(?:\s+for\s+that)?[.! ]*$",
        re.I,
    )
    _WEATHER_CORRECTION = re.compile(
        r"^\s*(?:no\s+(?:rain|storm|snow)|(?:it(?:'s| is)\s+)?not\s+"
        r"(?:raining|rainy|snowing))\b[^?]*[.! ]*$",
        re.I,
    )
    _HOT_NO_RAIN_UPDATE = re.compile(
        r"^[^?]{0,180}\b(?:hot|warm|humid|muggy|stuffy)\b[^?]{0,120}"
        r"\b(?:no rain|not raining|isn['’]t raining)\b[^?]*[.! ]*$",
        re.I,
    )
    _CASUAL_CLOSE = re.compile(
        r"^\s*(?:that(?:'ll| will) do|enough for now|we['’]re done)"
        r"(?:[,!. ]+(?:thanks|thank you|merci))?[.! ]*$",
        re.I,
    )

    def handle(self, text: str) -> ResponseCandidate | None:
        if closed_repetition_feedback(text):
            # This is an acknowledgement, not a regenerated brief or a claim
            # that a preference was saved or a scheduling defect repaired.
            return ResponseCandidate(
                "You’re right—that was a repeat, not a useful update.",
                "social:repetition_feedback",
            )
        if self._PRESENCE_CHECK.fullmatch(text.strip()):
            return ResponseCandidate(
                "Bonjour. Awake and ready.", "social:presence_check"
            )
        if self._CASUAL_CLOSE.fullmatch(text.strip()):
            return ResponseCandidate("Anytime.", "social:casual_close")
        if self._FRENCH_WELLBEING.fullmatch(text.strip()):
            return ResponseCandidate(
                "Ça va bien, merci. Et toi?", "social:french_check_in"
            )
        if self._NO_TOOL_NEEDED.fullmatch(text.strip()):
            return ResponseCandidate(
                "Got it. No query needed.", "social:no_tool_acknowledgement"
            )
        if weather_observation(text) and (
            self._WEATHER_CORRECTION.fullmatch(text.strip())
            or self._HOT_NO_RAIN_UPDATE.fullmatch(text.strip())
        ):
            reply = (
                "Oof. That kind of stuffy heat is miserable."
                if re.search(r"\b(?:hot|warm|humid|muggy|stuffy)\b", text, re.I)
                else "Got it. No rain, then."
            )
            return ResponseCandidate(reply, "social:weather_correction")
        acknowledgement = self._ACKNOWLEDGEMENT.fullmatch(text.strip())
        if acknowledgement:
            normalized = text.strip().casefold()
            reply = (
                "Anytime." if normalized.startswith(("thank", "cheers")) else "Good."
            )
            return ResponseCandidate(reply, "social:acknowledgement")
        # Outcomes, corrections and banter need conversation context. Keyword
        # shortcuts mistook "haven't fixed" for success and "that was not what
        # I asked" for praise. Let the conversational model interpret them.
        if not self._GREETING.fullmatch(text):
            return None
        if re.search(r"\b(?:how are you|how(?:'s| is) it going)\b", text, re.I):
            # Let the model use relationship history for genuine conversation
            # instead of repeating one canned line forever.
            return None
        return ResponseCandidate("Bonjour! How are you?", "social")
