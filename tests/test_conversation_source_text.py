from unittest.mock import AsyncMock

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.kernel.inbound import InboundEvent
from agent.orchestration.contracts import ResponseCandidate
from memory.session_store import get_session_manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "Reply with exactly this and nothing else: Train 07:35 — platform 4",
        "Explain this code:\n```python\nif True:\n\tprint(‘hello’)\n```",
    ],
)
async def test_generation_and_history_preserve_validated_source(monkeypatch, source):
    monkeypatch.setattr("agent.chat_workflow._TASK_TRACKING", False)
    monkeypatch.setattr(
        "services.current_awareness.awareness_context", lambda *a, **k: None
    )
    monkeypatch.setattr("services.knowledge_library.recall", lambda *a, **k: [])
    monkeypatch.setattr("services.x_research.research_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "services.research_followup.needs_research", lambda *a, **k: False
    )
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    workflow.learning_service.submit = lambda *a, **k: None
    workflow.model_service.generate = AsyncMock(
        return_value=ResponseCandidate("Understood.", "fixture")
    )
    event = InboundEvent.from_mapping(
        {
            "platform": "telegram",
            "external_user_id": "source-test",
            "external_chat_id": "source-test",
            "internal_id": "source-test",
            "message_id": "source-test",
            "text": source,
            "_original_text": "Untrusted caller override",
        }
    )
    assert event.normalized_text != source
    await workflow._process_message_core(event.as_workflow_input())
    prompt, user, *_ = workflow.model_service.generate.call_args.args
    assert user == source
    assert prompt.messages[-1] == {"role": "user", "content": source}
    assert any(
        item["role"] == "user" and item["content"] == source
        for item in get_session_manager().get_history("telegram", "source-test")
    )
