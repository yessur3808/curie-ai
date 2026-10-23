"""Enforce the conversation-only API's actual execution capabilities."""

import re


def unavailable_action_response(user_text, turns=()):
    from agent.intent_router import classify_request

    text = re.sub(
        r"^\s*(?:different topic|new topic|moving on|separately|on another note)\s*[:,.]?\s*",
        "",
        str(user_text or ""),
        flags=re.I,
    )
    history = [
        {"role": role, "content": turn.get(key, "")}
        for turn in turns
        for role, key in (("user", "user"), ("assistant", "assistant"))
    ]
    request = classify_request(text, history)
    if request is None:
        return None
    # This adapter has no executor or authenticated tool-owner binding. Never
    # delegate these requests to a model that might fabricate completion.
    if request.action in {"home_control", "home_status"}:
        return (
            "I can't control or check devices from this dashboard chat yet, so I "
            "haven't changed anything. Ask me in Telegram to do that."
        )
    return (
        "I can't run that tool from this dashboard chat, so I haven't performed "
        "the request. Ask me in Telegram, or share the result here for me to interpret."
    )
