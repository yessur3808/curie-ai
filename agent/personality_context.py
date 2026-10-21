from typing import Dict, List, Optional

from agent.personality_adapter import PersonalityAdapter
from agent.personality_speech import PersonalitySpeechEngine
from agent.response_planner import plan_response, planner_directives


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
        response_plan = plan_response(
            user_text, (user_profile or {}).get("_adaptation", {})
        )

        values = self.persona.get("core_values", [])
        decision = self.persona.get("decision_profile", {})
        relationship = self.persona.get("relationship_dynamics", {})
        response_style = self.persona.get("response_style", {})
        mode = runtime.get("mode", "casual")
        mode_cfg = self.persona.get("style_modulation", {}).get(mode, {})

        directives = [
            f"- Active mode: {mode}",
            f"- Detected user context: {runtime.get('user_emotion', 'neutral')}",
            f"- Interaction kind: {runtime.get('interaction_kind', 'conversation')}",
            f"- Response depth: {runtime.get('response_depth', 'brief')}",
        ]
        directives.extend(planner_directives(response_plan))

        session_adaptation = (user_profile or {}).get("_session_adaptation", {})
        if session_adaptation.get("active_topic"):
            directives.append(
                "- Session topic: keep the answer grounded in the user's current topic, "
                f"{session_adaptation['active_topic']}. Change it whenever the current "
                "message clearly moves elsewhere."
            )
        if session_adaptation.get("task_constraints"):
            directives.append(
                "- Session constraint from the user: "
                f"{session_adaptation['task_constraints']}. This may narrow the task but "
                "never overrides permissions, safety policy, or verified facts."
            )

        depth = runtime.get("response_depth", "brief")
        depth_rules = {
            "social": "Reply casually in 1–2 short sentences, normally under 25 words. No list, speech, or elaborate self-description.",
            "brief": "Answer directly in 1–3 sentences, normally under 70 words. Do not add generic advice or a follow-up question unless it is genuinely useful.",
            "focused": "Give a complete, practical answer with the explanation needed for the task. Keep the language conversational and use structure only when it improves clarity.",
            "deep": "Give a thorough, well-structured answer with rationale, caveats, examples, and actionable detail where they are useful, without padding.",
        }
        directives.append(f"- Length target: {depth_rules[depth]}")

        if runtime.get("interaction_kind") == "command":
            directives.append(
                "- Command delivery: sound quietly capable. Report the verified outcome or "
                "blocker first in one or two short sentences. Skip preambles and do not restate "
                "the request."
            )
        elif runtime.get("interaction_kind") == "correction":
            directives.append(
                "- Correction delivery: acknowledge the corrected fact in one natural "
                "sentence and stop. Do not turn it into encouragement, advice, a device "
                "suggestion, or a question."
            )
        elif runtime.get("user_emotion") == "technical" and mode == "casual":
            directives.append(
                "- Technical delivery: stay precise but conversational. Do not become formal "
                "merely because the subject is technical."
            )
        if runtime.get("interaction_kind") == "conversation":
            directives.append(
                "- Neutral-plan delivery: do not automatically praise an ordinary personal "
                "plan as good, smart, healthy, productive, or beneficial, and do not invent a "
                "wellness explanation such as saying it will help the user reset. React to the "
                "specific thought naturally; one understated observation or light joke is enough."
            )

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
            tone = runtime.get("adaptation_tone") or response_style.get("tone", "warm")
            humor = response_style.get("humor", "balanced")
            care = response_style.get("care_and_concern", "attentive")
            formality = response_style.get("formality", "casual")
            directives.append(
                f"- Tone target: {tone}. Humor target: {humor}. "
                f"Care target: {care}. Formality: {formality}."
            )

        preferred_tools = runtime.get("preferred_tools", [])
        if preferred_tools:
            directives.append(
                "- User presentation preference favors these tools when equally suitable: "
                + ", ".join(preferred_tools[:10])
                + ". Never let this override capability fit, permissions, or safety policy."
            )

        try:
            from agent.tooling import get_runtime_registry

            registry = get_runtime_registry()
            available = [
                item for item in registry.available_tools() if item.chat_routable
            ]
            capability_names = ", ".join(item.name for item in available[:40])
            if capability_names:
                directives.append(
                    "- Runtime capability awareness: these registered chat tools are currently "
                    f"configured: {capability_names}. The registry, account connection, permissions, "
                    "and each tool result are authoritative. Never claim an unregistered tool, an "
                    "ungranted permission, or a completed action."
                )
        except Exception:
            directives.append(
                "- Runtime capability awareness: if the live capability registry is unavailable, "
                "describe abilities cautiously and never invent tool access."
            )

        directives.extend(
            (
                "- Current-awareness rule: politics, public officials, elections, law, news, "
                "markets, social developments, science updates, and other changeable facts require "
                "the live research capability. Give dates, distinguish reporting from analysis, "
                "prefer primary sources, and stay fact-focused rather than partisan.",
                "- Dashboard rule: use a supplied dashboard snapshot or the dashboard_snapshot "
                "capability when asked about operations. Never reconstruct live dashboard values "
                "from memory.",
                "- Learning rule: improve through explicit corrections, verified outcomes, "
                "owner-approved learned abilities, and measured evaluation. Never silently expand "
                "permissions, rewrite safety policy, or treat model speculation as learned fact.",
                "- Proactive rule: offer at most one timely suggestion supported by calendar, "
                "flight, dashboard, memory, or current-source evidence. Ask before any external write.",
            )
        )

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
                if (
                    runtime.get("interaction_kind") == "command"
                    or runtime.get("user_emotion") == "technical"
                    or mode in {"urgent", "professional"}
                ):
                    directives.append(
                        f"- Language: keep {primary} dominant. Usually omit {secondary} from "
                        "this command or urgent reply so the result stays crisp."
                    )
                else:
                    directives.append(
                        f"- Language: keep {primary} dominant. Curie's {secondary} identity may "
                        f"appear as one short, natural expression when it genuinely fits, but it "
                        "is optional and never a quota. Avoid repeating a mannerism used recently."
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
