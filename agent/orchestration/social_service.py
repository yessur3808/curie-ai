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
    _TECHNICAL_WIN = re.compile(
        r"\b(?:finally\s+)?(?:fixed|solved|resolved|squashed)\b.{0,60}"
        r"\b(?:bug|issue|error)\b|"
        r"\b(?:bug|issue|error)\b.{0,60}\b(?:fixed|solved|resolved|squashed)\b",
        re.I | re.S,
    )
    _DEEP_FOLLOWUP = re.compile(
        r"\b(?:why|how|explain|analy[sz]e|root cause|deep dive)\b", re.I
    )
    _NEXT_STEP_REQUEST = re.compile(
        r"\b(?:(?:one|a) next step|what (?:should i do|comes) next)\b", re.I
    )
    _CASUAL_QUIP = re.compile(
        r"^(?:that was|well,?\s+that was|not bad|nice one|well played|look at you)"
        r"\b.{0,100}[.!?]*$",
        re.I | re.S,
    )
    _ACKNOWLEDGEMENT = re.compile(
        r"^(?:thanks(?: a lot)?|thank you|cheers|got it|okay|ok|cool|perfect|"
        r"sounds good|all right|alright)[.! ]*$",
        re.I,
    )
    _FACT_CORRECTION = re.compile(
        r"^(?:no[,!. ]+)?(?:there (?:is|are) no(?: such)?\s+.+|"
        r"there(?:['’]s| is) none)[.! ]*$",
        re.I,
    )
    _DAYTIME_LIGHT_PREFERENCE = re.compile(
        r"^.{0,120}\b(?:do not|don['’]t) need (?:the )?lights? "
        r"(?:switched |turned )?on during the day[.! ]*$",
        re.I,
    )

    def handle(self, text: str) -> ResponseCandidate | None:
        acknowledgement = self._ACKNOWLEDGEMENT.fullmatch(text.strip())
        if acknowledgement:
            normalized = text.strip().casefold()
            reply = (
                "Anytime." if normalized.startswith(("thank", "cheers")) else "Good."
            )
            return ResponseCandidate(reply, "social:acknowledgement")
        if self._DAYTIME_LIGHT_PREFERENCE.fullmatch(text.strip()):
            return ResponseCandidate(
                "Got it. They can stay off.", "social:preference_acknowledgement"
            )
        if self._FACT_CORRECTION.fullmatch(text.strip()):
            if re.search(r"\bprojects?\b", text, re.I):
                reply = "You're right. I made an assumption there."
            else:
                reply = "Understood."
            return ResponseCandidate(reply, "social:correction")
        if self._TECHNICAL_WIN.search(text) and not self._DEEP_FOLLOWUP.search(text):
            reply = "Nicely done. That bug was getting far too comfortable."
            if self._NEXT_STEP_REQUEST.search(text):
                reply += (
                    " Run the closest regression test, then commit the fix while the cause "
                    "is still fresh."
                )
            return ResponseCandidate(reply, "social:technical_win")
        if self._CASUAL_QUIP.fullmatch(text.strip()):
            if re.search(r"\bsuspiciously\s+efficient\b", text, re.I):
                reply = "I prefer ‘efficient enough to raise questions.’"
            elif re.match(r"^\s*(?:not bad|nice one)\b", text, re.I):
                reply = "I will accept that as high praise."
            else:
                reply = "A tidy result. I do enjoy those."
            return ResponseCandidate(reply, "social:casual_quip")
        if not self._GREETING.fullmatch(text):
            return None
        if re.search(r"\b(?:how are you|how(?:'s| is) it going)\b", text, re.I):
            # Let the model use relationship history for genuine conversation
            # instead of repeating one canned line forever.
            return None
        return ResponseCandidate("Bonjour! What is on your mind?", "social")
