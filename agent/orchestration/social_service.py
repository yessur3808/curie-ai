"""Fast deterministic handling for trivial social greetings."""

from __future__ import annotations

import re

from agent.orchestration.contracts import ResponseCandidate


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
        if self._WEATHER_CORRECTION.fullmatch(
            text.strip()
        ) or self._HOT_NO_RAIN_UPDATE.fullmatch(text.strip()):
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
        return ResponseCandidate("Bonjour! How are you?", "social")
