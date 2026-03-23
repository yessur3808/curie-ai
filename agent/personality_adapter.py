import re
from typing import Dict, List, Optional


class PersonalityAdapter:
    """Infers conversation context and returns personality modulation hints."""

    _DISTRESS_PATTERNS = re.compile(
        r"\b(help|scared|afraid|anxious|panic|overwhelmed|stressed|hurt|sad|depressed|crisis)\b",
        re.IGNORECASE,
    )
    _TECHNICAL_PATTERNS = re.compile(
        r"\b(code|python|bug|error|stack|api|database|algorithm|optimi[sz]e|debug|architecture|model)\b",
        re.IGNORECASE,
    )
    _CELEBRATION_PATTERNS = re.compile(
        r"\b(yes|yay|great|awesome|amazing|won|success|passed|done|finally)\b",
        re.IGNORECASE,
    )
    _URGENT_PATTERNS = re.compile(
        r"\b(urgent|asap|immediately|now|quick|emergency|right away)\b",
        re.IGNORECASE,
    )

    def infer_context(
        self,
        user_text: str,
        user_profile: Optional[Dict] = None,
        history: Optional[List] = None,
    ) -> Dict:
        text = (user_text or "").strip()

        user_emotion = "neutral"
        if self._DISTRESS_PATTERNS.search(text):
            user_emotion = "distress"
        elif self._CELEBRATION_PATTERNS.search(text):
            user_emotion = "celebratory"
        elif self._TECHNICAL_PATTERNS.search(text):
            user_emotion = "technical"

        urgency = bool(self._URGENT_PATTERNS.search(text))
        mode = (
            "urgent"
            if urgency
            else ("professional" if user_emotion == "technical" else "casual")
        )

        trust_signal = "new"
        history_len = len(history or [])
        if history_len >= 20:
            trust_signal = "high"
        elif history_len >= 6:
            trust_signal = "medium"

        return {
            "mode": mode,
            "user_emotion": user_emotion,
            "urgency": urgency,
            "history_len": history_len,
            "trust_signal": trust_signal,
            "user_profile_keys": sorted(list((user_profile or {}).keys())),
        }
