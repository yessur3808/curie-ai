import random
from typing import Dict, Optional


class PersonalitySpeechEngine:
    """Applies lightweight post-generation speech traits for active persona."""

    def apply(self, response: str, persona: Dict, context: Optional[Dict] = None) -> str:
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
        intensity = float(modulation.get("french_intensity", 0.2))

        # Keep French light and natural: at most 1 phrase most of the time.
        if intensity <= 0:
            return response

        seed = hash((response[:80], context.get("user_emotion", "neutral"), mode))
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
        accent_intensity = float(modulation.get("accent_intensity", 0.2))
        if accent_intensity < 0.2:
            return response

        replacements = persona.get("speech_pattern", {}).get("accent", {}).get(
            "modifications", {}
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
