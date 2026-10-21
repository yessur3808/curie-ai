"""Offline regression gate for natural multi-turn conversation flow."""

from __future__ import annotations

import asyncio
import json

from agent.kernel.dialogue_state import DialogueStateStore, TransitionClass
from agent.orchestration.response_policy import ResponsePolicy
from agent.orchestration.social_service import SocialConversationService
from agent.routing import route_request
from contracts.catalog import handle_capabilities_command
from services.trained_voice.voice_persona import (
    dashboard_scope_prompt,
    handle_conversation_contract,
    is_topic_regression,
    is_unhelpful_repeat,
    select_relevant_history,
    turn_contract_prompt,
)


class _IdentityStyle:
    def apply_response_style(self, response, *_args, **_kwargs):
        return response


async def _route(text: str, history=None):
    return await route_request(
        text,
        history=history,
        allow_model_inference=False,
    )


def run() -> dict:
    """Replay production-shaped turns without a model, network, or live tools."""

    async def evaluate_routes():
        inputs = (
            "Bonjour",
            "Ca va?",
            "No rain, quite hot and stuffy actually",
            "No need to use any query",
            "I bought a new rain jacket today.",
        )
        return {text: await _route(text) for text in inputs}

    decisions = asyncio.run(evaluate_routes())
    social = SocialConversationService()
    replies = {
        text: (candidate.text if (candidate := social.handle(text)) else "")
        for text in (
            "Bonjour",
            "Ca va?",
            "No rain, quite hot and stuffy actually",
            "No need to use any query",
            "Bonjour, you alive in there?",
            "I'm surviving. It's hot and stuffy here, but at least there is no rain.",
            "That'll do, merci.",
        )
    }

    restarted = DialogueStateStore()
    direct_answer = restarted.transition_for(
        "No rain, quite hot and stuffy actually",
        platform="telegram",
        owner_id="evaluation",
        history=[
            {"role": "user", "content": "Ca va?"},
            {
                "role": "assistant",
                "content": "How are you? Has it been raining there?",
            },
        ],
    )

    insistence_turns = [
        {
            "user": "I want every memory stored forever.",
            "assistant": (
                "I would not recommend that. Permanent retention creates privacy "
                "and relevance problems; important memories need review and expiry rules."
            ),
        }
    ]
    insistence_history = select_relevant_history(
        insistence_turns,
        "Suppose I insist.",
    )
    repeated_reply = is_unhelpful_repeat(
        insistence_turns[0]["assistant"],
        insistence_turns,
        "Suppose I insist.",
    )
    regression_turns = [
        {
            "user": "What does turn those off mean?",
            "assistant": "It means the workshop lights; ask if the reference is unclear.",
        },
        insistence_turns[0],
    ]
    regression_context = select_relevant_history(regression_turns, "Suppose I insist.")
    old_topic_reply = (
        "I would ask which workshop lights should be turned off because the "
        "reference is unclear."
    )

    low_confidence = handle_capabilities_command(
        "If your confidence in a device match were seventy-two percent, would you execute the command?"
    )
    plural_device = handle_capabilities_command(
        "Suppose I said, 'turn off both devices.' How would you work out which two I meant?"
    )

    policy = ResponsePolicy(
        persona={"name": "Curie"},
        personality_context=_IdentityStyle(),
    )
    nonredundant = policy.finalize(
        (
            "The workshop lights have been on for twenty minutes, the flight is delayed, "
            "and the meeting starts in twelve minutes. The meeting comes first. "
            "Would you like help with anything else?"
        ),
        user_text=(
            "The workshop lights have been on for twenty minutes, the flight is delayed, "
            "and my meeting starts in twelve minutes. What matters first?"
        ),
        history=[],
    )
    proactive = policy.finalize(
        "The meeting comes first. I can check the delayed flight next.",
        user_text="What matters first?",
        history=[],
    )
    scope = dashboard_scope_prompt(False)
    memory_answer = handle_conversation_contract(
        "I am redesigning your memory system and want every memory stored forever.",
        [],
    )
    reminder_answer = handle_conversation_contract(
        "What would you need from me before you add a reminder for tomorrow at 7 p.m.?",
        [],
    )
    natural_capabilities = handle_capabilities_command(
        "Ha, good. What can you actually help me with from here? Not the marketing version, just what is genuinely useful."
    )

    checks = {
        "greeting_uses_social_fast_path": decisions["Bonjour"].intent == "social",
        "french_checkin_uses_social_fast_path": decisions["Ca va?"].intent == "social",
        "weather_correction_is_not_a_tool_query": decisions[
            "No rain, quite hot and stuffy actually"
        ].intent
        in {"social", "conversation"},
        "query_rejection_is_not_a_clarification": decisions[
            "No need to use any query"
        ].intent
        in {"social", "conversation"},
        "incidental_rain_word_does_not_activate_tools": decisions[
            "I bought a new rain jacket today."
        ].intent
        == "conversation",
        "greeting_reply_is_natural": replies["Bonjour"] == "Bonjour! How are you?",
        "french_checkin_reply_is_natural": replies["Ca va?"]
        == "Ça va bien, merci. Et toi?",
        "weather_correction_is_acknowledged": replies[
            "No rain, quite hot and stuffy actually"
        ]
        == "Ah, got it. Hot and stuffy, not rainy.",
        "tool_rejection_is_acknowledged": replies["No need to use any query"]
        == "Got it. No query needed.",
        "presence_check_is_brief_and_ready": replies["Bonjour, you alive in there?"]
        == "Bonjour. Awake and ready.",
        "embedded_weather_update_is_natural": replies[
            "I'm surviving. It's hot and stuffy here, but at least there is no rain."
        ]
        == "Ah, got it. Hot and stuffy, not rainy.",
        "casual_close_has_no_empty_offer": replies["That'll do, merci."] == "Anytime.",
        "assistant_question_survives_restart": direct_answer.kind
        is TransitionClass.DIRECT_ANSWER,
        "insistence_keeps_immediate_context": bool(insistence_history),
        "recycled_reply_is_detected": repeated_reply,
        "older_topic_reply_is_detected": is_topic_regression(
            old_topic_reply,
            regression_context,
            regression_turns,
            "Suppose I insist.",
        ),
        "continuation_contract_names_immediate_exchange": "immediately preceding exchange"
        in turn_contract_prompt("Suppose I insist.", insistence_turns),
        "low_confidence_device_match_abstains": bool(
            low_confidence
            and "would not execute" in low_confidence.lower()
            and "90%" in low_confidence
        ),
        "plural_device_reference_never_guesses": bool(
            plural_device
            and any(
                marker in plural_device.lower()
                for marker in ("which", "clarification", "verified", "guessing")
            )
        ),
        "redundant_situation_recap_is_removed": nonredundant
        == "The meeting comes first. I can check the flight details next.",
        "specific_proactive_solution_survives": "check the delayed flight"
        in proactive.lower(),
        "prompt_requires_answer_first_no_parroting": all(
            marker in scope.lower()
            for marker in (
                "answer first",
                "do not repeat or paraphrase",
                "mention one specific next action",
            )
        ),
        "self_memory_answer_is_grounded_in_software_design": bool(
            memory_answer
            and "retrieval quality" in memory_answer
            and "human brain" not in memory_answer
        ),
        "reminder_requirements_use_supplied_time": bool(
            reminder_answer and "stated day and time are enough" in reminder_answer
        ),
        "prefaced_capability_question_uses_registry": bool(
            natural_capabilities
            and "What is genuinely useful right now" in natural_capabilities
            and "active" not in natural_capabilities.casefold()
        ),
    }
    return {
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "pass_rate": round(sum(checks.values()) / len(checks), 4),
    }


def main() -> int:
    result = run()
    for name, passed in result["checks"].items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    print(json.dumps({key: value for key, value in result.items() if key != "checks"}))
    return 0 if result["pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
