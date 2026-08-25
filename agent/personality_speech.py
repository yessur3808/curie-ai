import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _trim_social_reply(response: str, max_words: int = 28) -> str:
    """Bound pure small talk without shortening substantive answers."""
    sentences = re.findall(r".+?(?:[.!?](?=\s|$)|$)", response.strip(), re.DOTALL)
    selected = " ".join(sentence.strip() for sentence in sentences[:2]).strip()
    words = selected.split()
    if len(words) <= max_words:
        return selected
    first = sentences[0].strip() if sentences else selected
    if len(first.split()) <= max_words:
        return first
    return " ".join(first.split()[:max_words]).rstrip(",;:-") + "…"


def _trim_brief_reply(response: str, max_words: int = 85) -> str:
    """Enforce a useful ceiling for simple questions without clipping code or lists."""
    if "```" in response or len(response.split()) <= max_words:
        return response
    sentences = re.findall(r".+?(?:[.!?](?=\s|$)|$)", response.strip(), re.DOTALL)
    selected: list[str] = []
    for sentence in sentences[:3]:
        if len((" ".join(selected + [sentence.strip()])).split()) > max_words:
            break
        selected.append(sentence.strip())
    if selected:
        return " ".join(selected)
    return " ".join(response.split()[:max_words]).rstrip(",;:-") + "…"


def _safe_float(value, default=0.2):
    """Safely convert value to float, logging errors and returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.debug(f"Could not convert {value!r} to float, using default {default}")
        return default


class PersonalitySpeechEngine:
    """Applies lightweight post-generation speech traits for active persona."""

    def apply(
        self, response: str, persona: Dict, context: Optional[Dict] = None
    ) -> str:
        if not response or response.startswith("[Error"):
            return response

        name = (persona.get("name") or "").strip().lower()
        if name == "curie":
            return self._apply_curie_speech(response, persona, context or {})
        if name == "andreja":
            return self._apply_andreja_speech(response, persona, context or {})
        return response

    def _apply_curie_speech(self, response: str, persona: Dict, context: Dict) -> str:
        language_profile = persona.get("language_profile", {})
        french_config = language_profile.get("french_integration", {})
        if not french_config.get("enabled", False):
            return response

        phrases = persona.get("french_phrases", [])
        if not phrases:
            return response

        mode = context.get("mode", "casual")
        if mode == "urgent":
            return response
        if context.get("response_depth") == "social":
            response = _trim_social_reply(response)
        elif context.get("response_depth") == "brief":
            response = _trim_brief_reply(response)
        modulation = persona.get("style_modulation", {}).get(mode, {})
        intensity = _safe_float(modulation.get("french_intensity", 0.2), default=0.2)

        # The model prompt owns Curie's voice. Post-processing is only a light
        # fallback when the generated reply contains no French at all.
        if intensity <= 0:
            return response

        # The prompt owns the bilingual voice. Automatically inserting phrases
        # into generated sentences produced awkward or incorrect French.
        return response

    def _apply_andreja_speech(self, response: str, persona: Dict, context: Dict) -> str:
        profile = persona.get("language_profile", {})
        if not profile.get("accent_enabled", False):
            return response

        mode = context.get("mode", "casual")
        modulation = persona.get("style_modulation", {}).get(mode, {})
        accent_intensity = _safe_float(
            modulation.get("accent_intensity", 0.2), default=0.2
        )
        if accent_intensity < 0.2:
            return response

        replacements = (
            persona.get("speech_pattern", {}).get("accent", {}).get("modifications", {})
        )
        if not replacements:
            return response

        transformed = response
        max_replacements = 1 if accent_intensity < 0.4 else 2
        replacements_done = 0

        for source, target in replacements.items():
            if replacements_done >= max_replacements:
                break
            if source in transformed and len(source) > 1:
                transformed = transformed.replace(source, target, 1)
                replacements_done += 1

        return transformed
