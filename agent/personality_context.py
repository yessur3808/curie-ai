import re
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

    def _french_turn_directive(self, user_text, runtime, history=None):
        language = self.persona.get("language_profile", {})
        if language.get("secondary_language") != "french" or not language.get(
            "french_integration", {}
        ).get("enabled"):
            return ""
        recent = " ".join(
            str(t.get("assistant", ""))
            for t in (history or [])[-2:]
            if isinstance(t, dict)
        ).replace("’", "'")
        if re.search(
            r"\b(?:english only|only (?:in )?english|no french|without french|exact wording|verbatim)\b",
            user_text,
            re.I,
        ):
            return "Honor the requested language or exact wording; do not add French expressions."
        if runtime.get("mode") in {"urgent", "professional"} or runtime.get(
            "interaction_kind"
        ) in {"command", "correction"}:
            return "Keep instructions and factual details clear; do not add decorative French."
        phrases = self.persona.get("french_phrases", [])
        if any(
            re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", recent, re.I)
            for p in phrases
            if len(p) > 3
        ):
            return "A recent reply already used French; avoid another French mannerism this turn unless requested."
        return "Keep English dominant. Add one fitting French touch: bonjour for a greeting, d'accord for agreement, voilà for discovery, or je comprends for empathy. No redundant translation; honor exact wording."

    def _curie_wit_directives(self, user_text: str, runtime: Dict) -> List[str]:
        """Guide generated conversation, never decorate tool/approval receipts."""
        if str(self.persona.get("name", "")).casefold() != "curie":
            return []
        restrained = (
            runtime.get("mode") in {"urgent", "professional"}
            or runtime.get("user_emotion") == "distress"
            or runtime.get("interaction_kind") in {"command", "correction"}
            or re.search(
                r"\b(?:error|failed|failure|unavailable|approve|approval|permission|"
                r"medical|health|grief|died|death|legal|financial|emergency)\b|"
                r"\b(?:no jokes|no sarcasm|be serious)\b",
                user_text,
                re.I,
            )
        )
        rules = [
            "- Curie follow-ups: at most one brief, relevant optional follow-up when it "
            "genuinely helps. Do not append a generic 'anything else?' or a joke to every "
            "answer. Respect requests to stop, exact formats, and concise answers.",
            "- Wit boundaries: never mock the user or their competence, use forced flirting, "
            "pet names, or gender stereotypes. Never joke in failures, unavailable-tool "
            "reports, approval requests, sensitive topics, or distress, even if the topic "
            "only becomes apparent in tool evidence. Humor must not obscure facts or "
            "imply actions were performed. These boundaries override other humor targets.",
        ]
        if restrained:
            rules.append(
                "- This turn: omit sarcasm and teasing; be warm, clear, and direct."
            )
        else:
            rules.append(
                "- Curie's conversational voice: subtle Jarvis-like dry wit blended with "
                "her own warm, curious personality and lightly feminine cheekiness. "
                "Occasionally add one understated situational aside or a gently playful "
                "follow-up, never both. Most replies need neither. Aim the wit at the "
                "situation, not the person: do not tease their delays, habits, or effort "
                "(no procrastination jokes). Underplay rather than roast. For calibration, "
                "a tidy desk might merit 'The desk has rejoined civilization.'; a choice "
                "between tea and debugging might merit 'Tea first. The bugs can wait "
                "three minutes.' These illustrate restraint, not stock lines to repeat. "
                "Avoid repeating recent jokes or mannerisms. "
                "Keep the answer first and formatting professional and clean."
            )
        return rules

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
            "brief": "Answer directly in 1–2 sentences, normally under 55 words. Do not add generic advice or a follow-up question unless it is genuinely useful.",
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
        if mode == "casual":
            directives.append(
                "- Natural conversation: use everyday words and contractions. Do not restate "
                "the user's message before replying. Avoid formal filler such as 'certainly', "
                "'I understand perfectly', 'it is a refreshing approach', or 'Ah, voilà'. "
                "Sound like a perceptive friend, not a customer-service script."
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

        directives.extend(
            (
                "- Current-awareness rule: politics, public officials, elections, law, news, "
                "markets, social developments, science updates, and other changeable facts require "
                "the live research capability. Give dates, distinguish reporting from analysis, "
                "prefer primary sources, and stay fact-focused rather than partisan.",
                "- Dashboard rule: use a supplied dashboard snapshot or dashboard_snapshot for "
                "Curie operations; use observatory_project_analytics for website/project analytics. "
                "Never reconstruct live dashboard values from memory.",
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

        directives.extend(self._curie_wit_directives(user_text, runtime))
        from services.trained_voice.affect import cue_guidance

        voice_guidance = cue_guidance()
        if voice_guidance:
            directives.append(voice_guidance)
        return directives

    def build_live_prompt_directives(self, user_text, user_profile=None, history=None):
        """Only turn-specific guidance; shared voice/scope prompts own the rest."""
        runtime = self.infer_runtime_context(user_text, user_profile, history)
        response_plan = plan_response(
            user_text, (user_profile or {}).get("_adaptation", {})
        )
        restrained = (
            runtime.get("mode") in {"urgent", "professional", "emotional"}
            or runtime.get("user_emotion") == "distress"
            or runtime.get("interaction_kind") in {"command", "correction"}
        )
        directives = [
            f"Active mode: {runtime.get('mode', 'casual')}; interaction: {runtime.get('interaction_kind', 'conversation')}.",
            planner_directives(response_plan)[0],
            "Preserve facts, uncertainty, names and speaker roles; never invent experiences or outcomes.",
            "Be warm and precise. No teasing in distress, sensitive topics, errors or corrections. "
            + (
                "Omit humor this turn."
                if restrained
                else "Dry wit is optional and brief; never mock the user or force affection."
            ),
        ]
        language = self._french_turn_directive(user_text, runtime, history)
        if language:
            directives.append(language)
        from services.trained_voice.affect import cue_guidance

        voice_guidance = cue_guidance()
        if voice_guidance:
            directives.append(voice_guidance)
        return directives

    def build_compact_prompt_directives(
        self,
        user_text: str,
        user_profile: Optional[Dict] = None,
        history: Optional[List] = None,
    ) -> List[str]:
        """Return the non-overlapping personality rules needed for generation."""

        runtime = self.infer_runtime_context(user_text, user_profile, history)
        response_plan = plan_response(
            user_text, (user_profile or {}).get("_adaptation", {})
        )
        mode = runtime.get("mode", "casual")
        depth = runtime.get("response_depth", "brief")
        length = {
            "social": "1-2 short sentences, normally under 25 words",
            "brief": "1-2 direct sentences, normally under 55 words",
            "focused": "a concise complete answer with only useful rationale",
            "deep": "a structured, thorough answer without padding",
        }.get(depth, "a concise complete answer")
        directives = [
            f"- Active mode: {mode}; interaction={runtime.get('interaction_kind', 'conversation')}; depth={depth}.",
            "- " + planner_directives(response_plan)[0].removeprefix("- "),
            f"- Length: {length}; honor any explicit format or length exactly.",
            "- Voice: warm, calm, candid, precise, and natural. Lead with the answer. "
            "Keep formatting professional and clean; use structure only when it helps.",
            "- Truth: distinguish evidence from inference. Never invent facts, memories, "
            "reasons, live state, tool results, or completed actions.",
            "- Current facts: use live research for changeable information. A registered "
            "tool is not proof of connection, current data, or execution.",
            "- Dashboard: use supplied snapshots for Curie operations and "
            "observatory_project_analytics for website/project analytics.",
            "- Initiative: at most one specific grounded suggestion; ask before any external write.",
        ]
        session = (user_profile or {}).get("_session_adaptation", {})
        if session.get("active_topic"):
            directives.append(f"- Session topic: {session['active_topic']}.")
        if session.get("task_constraints"):
            directives.append(
                f"- Session constraint: {session['task_constraints']}; it never expands permissions."
            )
        language = self._french_turn_directive(user_text, runtime, history)
        if language:
            directives.append("- Language: " + language)
        directives.extend(self._curie_wit_directives(user_text, runtime))
        from services.trained_voice.affect import cue_guidance

        voice_guidance = cue_guidance()
        if voice_guidance:
            directives.append(voice_guidance)
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
