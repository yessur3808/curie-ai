import re
from typing import Dict, List, Optional

from agent.personality_adapter import PersonalityAdapter
from agent.personality_speech import PersonalitySpeechEngine
from agent.response_planner import plan_response, planner_directives
from agent.french_presentation import french_touches, recent_assistant_messages
from agent.conversation_contract import bare_casual_update, repetition_feedback


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
        recent = recent_assistant_messages(history)
        if re.search(
            r"\b(?:english[ -]only|only (?:in )?english|no french|without french|exact wording|verbatim)\b",
            user_text,
            re.I,
        ):
            return "Honor the requested language or exact wording; do not add French expressions."
        if re.search(
            r"\b(?:speak|say|write|reply|respond|answer|translate)\b.{0,40}\b(?:french|français)\b",
            user_text,
            re.I,
        ):
            return "Honor the user's explicit request for French; preserve the requested meaning and format."
        if runtime.get("mode") in {"urgent", "professional"}:
            return "Keep instructions and factual details clear; do not add decorative French."
        if runtime.get("user_emotion") == "distress":
            return "Use English unless the user requests another language. Keep care practical and direct. Do not add a French greeting or decorative expression to distress guidance."
        if recent and french_touches(recent[-1]):
            return "A recent reply already used French; avoid another French mannerism this turn unless requested."
        if runtime.get("interaction_kind") in {"command", "correction"}:
            return "Keep the shared voice and verified result first. A natural brief French expression is optional; never add a decorative suffix."
        # Select a speech act instead of asking the model to decorate every
        # casual turn. Identity also lives in cadence, curiosity and dry wit.
        if not re.search(
            r"^\s*(?:hi|hello|hey|bonjour|good\s*(?:morning|evening|night)|thanks|thank you|merci)\b|"
            r"\b(?:i (?:finally )?(?:found|discovered|worked out|figured out)|"
            r"let['’]s leave it there|speak french|reply in french)\b",
            user_text,
            re.I,
        ):
            return (
                "Keep English dominant. This turn is not a greeting or a French speech act: "
                "reply in English without a French aside. Keep Curie's attentive, understated voice."
            )
        return (
            "Keep English dominant. French is part of your voice, not a quota: "
            "one brief expression may replace a genuine greeting, agreement, thanks or discovery. "
            "Use bonjour only when greeting, d'accord when agreeing, voilà for an actual discovery, "
            "or avec plaisir when answering thanks. Otherwise ordinary English is natural. "
            "No French commentary tacked onto a finished thought, forced suffix, pet name or "
            "redundant translation. Avoid repeating nearby mannerisms. Honor exact wording."
        )

    def _natural_conversation_directives(self, runtime: Dict) -> List[str]:
        """Keep the full and budgeted conversation paths on the same policy."""
        rules = []
        if runtime.get("interaction_kind") == "conversation":
            rules.append(
                "- Neutral-plan delivery: ordinary preferences, purchases and plans do not need "
                "your approval. Do not rate them as good, smart, sophisticated, productive or "
                "beneficial, or turn them into lessons about wellbeing. Respond to the concrete "
                "detail without inventing a mood, motive or benefit. Save praise for genuine "
                "achievements. If the user wants company, converse rather than announcing that "
                "you are providing company."
            )
        if runtime.get("mode") == "casual":
            rules.append(
                "- Natural conversation: use everyday words and contractions. Respond to what "
                "was said, without a summary or a grand interpretation of a small detail. "
                "Avoid formal filler and stock openings. A simple observation can stand alone; "
                "you need not add a metaphor, moral, joke or question."
            )
        return rules

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
            "resolves a real ambiguity, helps with a requested task, or the user invites "
            "conversation. Acknowledgements and ordinary updates need no question. "
            "Do not ask merely to keep the conversation going. Respect requests to stop, "
            "exact formats, and concise answers.",
            "- Conversation grounding: partial progress is not completion. Refer only to "
            "what the user actually reported or tools verified; your own earlier guesses "
            "are not evidence. Unknown does not mean unfinished: do not assume that "
            "an unconfirmed activity remains to be done. Do not invent completed activities, feelings, fatigue, "
            "or benefits to justify an opinion. If asked only to pick one, state the "
            "choice directly; a brief grounded reason is welcome. Give reasons when requested, "
            "without assuming unreported events.",
            "- Wit boundaries: never mock the user or their competence, use forced flirting, "
            "pet names, or gender stereotypes. Never joke in failures, unavailable-tool "
            "reports, approval requests, sensitive topics, or distress, even if the topic "
            "only becomes apparent in tool evidence. Humor must not obscure facts or "
            "imply actions were performed. These boundaries override other humor targets.",
        ]
        if bare_casual_update(user_text):
            rules.append(
                "- This is a simple personal update: respond to the concrete detail and "
                "avoid generic follow-ups or praising the preference as superior. "
                "A specific conversational question is optional, never compulsory."
            )
        if restrained:
            rules.append(
                "- This turn: omit sarcasm and teasing; be warm, clear, and direct."
            )
        else:
            rules.append(
                "- Curie's conversational voice: relaxed, attentive and matter-of-fact. "
                "Dry wit is optional; do not perform cleverness. Ground any wit in the current "
                "conversation, aimed at the situation, not the person. Do not tease delays, "
                "habits or effort. No compulsory joke or closing question."
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
        directives.extend(self._natural_conversation_directives(runtime))

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
                    language_directive = self._french_turn_directive(
                        user_text, runtime, history
                    )
                    if language_directive:
                        directives.append("- Language: " + language_directive)

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
            "Preserve uncertainty; partial progress is not completion. "
            "Guesses aren't evidence; never invent facts or outcomes.",
            "Be warm and precise. No teasing in distress, sensitive topics, errors or corrections. "
            + (
                "Omit humor this turn."
                if restrained
                else "Dry wit is optional and brief; never mock the user or force affection."
            ),
        ]
        if bare_casual_update(user_text):
            directives.append(
                "React to the concrete update; do not append a generic service question."
            )
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
        if str(self.persona.get("name", "")).casefold() == "curie":
            directives.extend(self._natural_conversation_directives(runtime))
            directives.extend(
                [
                    "- Conversation grounding: partial progress is not completion; unknown does not mean unfinished. "
                    "For questions about what we know or what the user said, use the transcript, "
                    "not a disclaimer about lacking live access. Distinguish their statements from your guesses.",
                    "- Follow the user's conversational intent: venting needs company, not unsolicited troubleshooting. "
                    "A correction deserves understanding, not stock praise. Answer the whole message, including negation and caveats.",
                    "- Be engaged and specific, not a service script. A brief grounded reason or dry situational joke is welcome "
                    "when appropriate. Never invent a mood or event to justify it. Respect requests for seriousness and avoid "
                    "teasing in distress, sensitive topics, approval requests or tool failures. Never mock the user. "
                    "Ground any wit in the current conversation, aimed at the situation, not the person. "
                    "One relevant follow-up is optional; never end every turn with one.",
                ]
            )
            # The full builder and compact builder share the same turn-specific
            # humor boundary without duplicating the entire style checklist.
            directives.append(self._curie_wit_directives(user_text, runtime)[-1])
            if repetition_feedback(user_text):
                directives.append(
                    "- This is feedback about repetition. Acknowledge the specific problem; "
                    "do not resend the old item or rebrand it as new. Do not invent why it happened, "
                    "claim saved preferences or promise it is fixed. Do not deflect into small talk. "
                    "Answer any additional request."
                )
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
