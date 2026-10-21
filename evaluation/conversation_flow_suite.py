"""Offline regression gate for natural multi-turn conversation flow."""

from __future__ import annotations

import asyncio
import json

from agent.kernel.dialogue_state import DialogueStateStore, TransitionClass
from agent.orchestration.social_service import SocialConversationService
from agent.routing import route_request


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
        "assistant_question_survives_restart": direct_answer.kind
        is TransitionClass.DIRECT_ANSWER,
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
