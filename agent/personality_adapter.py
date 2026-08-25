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
    _SOCIAL_PATTERNS = re.compile(
        r"^(?:(?:hi+|hello+|hey+|bonjour|good\s+(?:morning|afternoon|evening))"
        r"(?:\s+curie)?[,!. ]*)?(?:how\s+are\s+you(?:\s+doing)?|how(?:'s|\s+is)\s+it\s+going)"
        r"[?!. ]*$|^(?:hi+|hello+|hey+|bonjour)(?:\s+curie)?[?!. ]*$",
        re.IGNORECASE,
    )
    _DEEP_PATTERNS = re.compile(
        r"\b(?:in depth|deep dive|detailed|thorough|comprehensive|step[- ]by[- ]step|"
        r"explain fully|full analysis|all the details|from first principles)\b",
        re.IGNORECASE,
    )
    _SUBSTANTIVE_PATTERNS = re.compile(
        r"\b(?:why|how|compare|analy[sz]e|research|plan|design|implement|debug|fix|"
        r"review|architecture|tradeoffs?|recommend|explain)\b",
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

        if self._SOCIAL_PATTERNS.fullmatch(text):
            response_depth = "social"
        elif self._DEEP_PATTERNS.search(text):
            response_depth = "deep"
        elif self._SUBSTANTIVE_PATTERNS.search(text) or len(text.split()) > 20:
            response_depth = "focused"
        else:
            response_depth = "brief"

        adaptation = (user_profile or {}).get("_adaptation", {})
        verbosity = adaptation.get("verbosity", "balanced")
        if response_depth not in {"social", "deep"} and not urgency:
            if verbosity == "concise":
                response_depth = "brief"
            elif verbosity == "detailed":
                response_depth = "deep" if response_depth == "focused" else "focused"
        if adaptation.get("research_depth") == "deep" and re.search(
            r"\b(?:research|investigate|compare sources?)\b", text, re.I
        ):
            response_depth = "deep"

        return {
            "mode": mode,
            "user_emotion": user_emotion,
            "urgency": urgency,
            "history_len": history_len,
            "trust_signal": trust_signal,
            "response_depth": response_depth,
            "adaptation_tone": adaptation.get("tone"),
            "preferred_tools": list(adaptation.get("preferred_tools", [])),
            "user_profile_keys": sorted(list((user_profile or {}).keys())),
        }
