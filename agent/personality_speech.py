import random
import hashlib
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


def _deterministic_seed(text: str, context: str, mode: str) -> int:
    """Generate a deterministic seed from input strings (hash-randomization safe)."""
    combined = f"{text}:{context}:{mode}"
    hash_bytes = hashlib.sha256(combined.encode()).digest()
    # Use first 8 bytes as an int seed
    return int.from_bytes(hash_bytes[:8], byteorder="big")


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
        modulation = persona.get("style_modulation", {}).get(mode, {})
        intensity = _safe_float(modulation.get("french_intensity", 0.2), default=0.2)

        # Keep French light and natural: at most 1 phrase most of the time.
        if intensity <= 0:
            return response

        seed = _deterministic_seed(
            response[:80], context.get("user_emotion", "neutral"), mode
        )
        rng = random.Random(seed)
        insert_phrase = rng.random() < min(max(intensity, 0.05), 0.5)
        if not insert_phrase:
            return response

        phrase = rng.choice(phrases)
        if response.endswith((".", "!", "?")):
            return f"{response} {phrase}"
        return f"{response}. {phrase}"

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
