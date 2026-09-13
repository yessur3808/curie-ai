"""Fast deterministic handling for trivial social greetings."""

from __future__ import annotations

import re

from agent.orchestration.contracts import ResponseCandidate


class SocialConversationService:
    _GREETING = re.compile(
        r"^\s*(?:hi|hello|hey|bonjour|salut)(?:\s+curie|\s+there)?"
        r"(?:[,!. ]+(?:how are you(?: doing)?|how(?:'s| is) it going))?[,!.? ]*$",
        re.I,
    )

    def handle(self, text: str) -> ResponseCandidate | None:
        if not self._GREETING.fullmatch(text):
            return None
        if re.search(r"\b(?:how are you|how(?:'s| is) it going)\b", text, re.I):
            # Let the model use relationship history for genuine conversation
            # instead of repeating one canned line forever.
            return None
        return ResponseCandidate("Bonjour! What is on your mind?", "social")
