import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition


@pytest.mark.parametrize(
    "prompt,query",
    [
        ("Please search Gmail for dinner, but do not send anything.", "dinner"),
        ("Search Gmail for lunch", "lunch"),
        (
            'Search Gmail for subject:"team dinner" from:alex@example.invalid',
            'subject:"team dinner" from:alex@example.invalid',
        ),
        (
            'Search Gmail for "dinner, but do not send anything"',
            '"dinner, but do not send anything"',
        ),
        ('Search Gmail for "dinner", but do not send anything.', '"dinner"'),
        ("/gmail search subject:dinner -in:trash", "subject:dinner -in:trash"),
    ],
)
def test_gmail_query_keeps_search_terms_not_execution_prohibition(
    monkeypatch, prompt, query
):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda self: (True, "sandbox")
    )
    calls = []

    async def blocked(name, params, context):
        calls.append((name, dict(params)))
        raise PermissionError("Sandbox: no account execution")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "gmail-query-boundary",
                "external_user_id": "gmail-query-boundary",
                "external_chat_id": "gmail-query-boundary",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == [("gmail_search", {"query": query})], result


@pytest.mark.parametrize(
    "prompt",
    [
        "For example, search Gmail for dinner",
        "Search Gmail for dinner is an example, not a command",
    ],
)
def test_illustrative_search_still_abstains(prompt):
    from agent.intent_router import classify_request

    assert classify_request(prompt) is None
