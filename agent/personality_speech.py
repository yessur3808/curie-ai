import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


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
        # Length is a generation target, not permission to delete sentences.
        # Post-hoc clipping lost answers, caveats and natural follow-up questions.
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
