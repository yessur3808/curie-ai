from typing import Dict, List, Optional

from agent.personality_adapter import PersonalityAdapter
from agent.personality_speech import PersonalitySpeechEngine


class PersonalityContext:
    """Central personality helper used by chat workflow and downstream services."""

    def __init__(self, persona: Dict):
        self.persona = persona or {}
        self.adapter = PersonalityAdapter()
        self.speech_engine = PersonalitySpeechEngine()

    def infer_runtime_context(
        self,
        user_text: str,
        user_profile: Optional[Dict] = None,
        history: Optional[List] = None,
    ) -> Dict:
        return self.adapter.infer_context(user_text, user_profile or {}, history or [])

    def build_prompt_directives(
        self,
        user_text: str,
        user_profile: Optional[Dict] = None,
        history: Optional[List] = None,
    ) -> List[str]:
        runtime = self.infer_runtime_context(user_text, user_profile, history)

        values = self.persona.get("core_values", [])
        decision = self.persona.get("decision_profile", {})
        relationship = self.persona.get("relationship_dynamics", {})
        response_style = self.persona.get("response_style", {})
        mode = runtime.get("mode", "casual")
        mode_cfg = self.persona.get("style_modulation", {}).get(mode, {})

        directives = [
            f"- Active mode: {mode}",
            f"- Detected user context: {runtime.get('user_emotion', 'neutral')}",
        ]

        if values:
            directives.append("- Core values to preserve: " + ", ".join(values[:5]))

        priority_order = decision.get("priority_order", [])
        if priority_order:
            directives.append(
                "- Decision priorities: " + " > ".join(priority_order[:5])
            )

        trust_signal = runtime.get("trust_signal", "new")
        if relationship:
            directives.append(
                f"- Relationship stance ({trust_signal} trust): "
                f"{relationship.get('default_view_of_user', 'helpful partner')}"
            )

        if response_style:
            tone = response_style.get("tone", "warm")
            humor = response_style.get("humor", "balanced")
            directives.append(f"- Tone target: {tone}; humor target: {humor}")

        if mode_cfg:
            directives.append(
                "- Style modulation: "
                + ", ".join([f"{k}={v}" for k, v in mode_cfg.items()])
            )

        language = self.persona.get("language_profile", {})
        if language:
            primary = language.get("primary_language", "english")
            secondary = language.get("secondary_language")
            if secondary:
                directives.append(
                    f"- Language: keep {primary} dominant; lightly blend {secondary} naturally."
                )

        return directives

    def apply_response_style(
        self,
        response: str,
        user_text: str,
        user_profile: Optional[Dict] = None,
        history: Optional[List] = None,
    ) -> str:
        runtime = self.infer_runtime_context(user_text, user_profile, history)
        return self.speech_engine.apply(response, self.persona, runtime)

    def get_response_temperature(self) -> float:
        settings = self.persona.get("settings", {})
        value = settings.get("default_temperature", 0.7)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.7
