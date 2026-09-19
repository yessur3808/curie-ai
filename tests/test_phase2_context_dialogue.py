import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.kernel.context import (
    ContextBudgeter,
    ContextCandidate,
    ContextSection,
)
from agent.kernel.dialogue_state import (
    DialogueStateStore,
    TransitionClass,
    classify_transition,
)
from agent.kernel.inbound import (
    EditedMessagePolicy,
    InboundEvent,
    InboundEventError,
)
from agent.kernel.summary import (
    extract_explicit_corrections,
    rewrite_summary_conflicts,
)
from agent.kernel.understanding import analyze_turn


def _event(**updates):
    data = {
        "platform": "telegram",
        "connector_account_id": "curie-main",
        "external_user_id": "owner",
        "external_chat_id": "chat",
        "message_id": "message-1",
        "text": "Hello Curie",
        "timestamp": datetime(2026, 9, 19, tzinfo=timezone.utc),
    }
    data.update(updates)
    return data


@pytest.mark.parametrize(
    "connector", ["telegram", "api", "discord", "slack", "whatsapp", "teams"]
)
def test_every_supported_connector_normalizes_to_one_inbound_contract(connector):
    event = InboundEvent.from_mapping(_event(platform=connector))

    assert event.connector == connector
    assert event.external_user_id == "owner"
    assert event.external_chat_id == "chat"
    assert event.message_id == "message-1"


def test_inbound_event_preserves_exact_text_while_normalizing_for_parsing():
    original = "  Turn\u00a0off  l’Éclairage—Nord\u200b  "
    event = InboundEvent.from_mapping(_event(text=original))

    assert event.original_text == original
    assert event.normalized_text == "Turn off l'Éclairage-Nord"


def test_inbound_attachments_are_typed_and_bounded():
    event = InboundEvent.from_mapping(
        _event(
            text="Review this",
            attachments=[
                {
                    "id": "photo-1",
                    "kind": "image",
                    "filename": "lamp.jpg",
                    "content_type": "image/jpeg",
                    "file_size": 2048,
                }
            ],
        )
    )

    assert event.attachments[0].attachment_id == "photo-1"
    assert event.attachments[0].media_type == "image/jpeg"
    assert event.attachments[0].size_bytes == 2048

    with pytest.raises(InboundEventError, match="attachment_too_large"):
        InboundEvent.from_mapping(
            _event(attachments=[{"id": "huge", "kind": "file", "size_bytes": 4097}]),
            max_attachment_bytes=4096,
        )


def test_edited_messages_have_an_explicit_revision_and_dedupe_policy():
    original = InboundEvent.from_mapping(_event())
    edited = InboundEvent.from_mapping(
        _event(
            event_kind="edited_message",
            edited_at="2026-09-19T01:02:03+00:00",
            text="Hello Curie, edited",
        )
    )

    assert edited.edit_policy is EditedMessagePolicy.PROCESS_REVISION
    assert edited.dedupe_key != original.dedupe_key
    with pytest.raises(InboundEventError, match="edited_message_ignored"):
        InboundEvent.from_mapping(
            _event(
                event_kind="edited_message",
                edited_at="2026-09-19T01:02:03+00:00",
                edited_message_policy="ignore",
            )
        )

    other_account = InboundEvent.from_mapping(
        _event(connector_account_id="curie-backup")
    )
    assert other_account.dedupe_key != original.dedupe_key

    with pytest.raises(InboundEventError, match="edited_message_missing_revision"):
        InboundEvent.from_mapping(_event(event_kind="edited_message"))


def test_oversized_input_is_quarantined_before_identity_or_model_work():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Helpful."})
    workflow.model_service.generate = AsyncMock()

    with (
        patch.dict("os.environ", {"CURIE_MAX_MESSAGE_CHARS": "20"}),
        patch(
            "agent.chat_workflow.UserManager.get_or_create_user_internal_id"
        ) as identity,
    ):
        result = asyncio.run(
            workflow.process_message(_event(text="x" * 21, internal_id=None))
        )

    assert result["input_status"] == "quarantined"
    assert result["input_error"] == "message_too_large"
    identity.assert_not_called()
    workflow.model_service.generate.assert_not_awaited()


def _observe(
    store,
    text,
    result,
    *,
    owner="owner",
    transition=None,
):
    analysis = analyze_turn(
        text,
        text,
        owner_id=owner,
        platform="telegram",
    )
    transition = transition or store.transition_for(
        text, platform="telegram", owner_id=owner
    )
    store.observe_for_owner(
        owner,
        analysis.state,
        result,
        text=text,
        transition=transition,
    )
    return analysis


def test_dialogue_state_values_always_include_provenance_expiry_and_owner_scope():
    store = DialogueStateStore()
    _observe(
        store,
        "Turn off both lights",
        {
            "text": "Should I proceed?",
            "execution_status": "waiting_approval",
            "approval_required": True,
            "_dialogue_entities": ["Desk Lamp", "Floor Lamp"],
        },
    )
    snapshot = store.snapshot(platform="telegram", owner_id="owner")

    for field_name in (
        "active_goal",
        "active_topic",
        "unresolved_assistant_question",
        "expected_answer_type",
        "referenced_entities",
        "pending_approval",
        "pending_task",
    ):
        value = getattr(snapshot, field_name)
        assert value is not None, field_name
        assert value.source_turn
        assert value.created_at > 0
        assert value.expires_at > value.created_at
        assert 0 <= value.confidence <= 1
        assert len(value.owner_scope) == 16


def test_explicit_device_correction_invalidates_stale_reference():
    store = DialogueStateStore()
    _observe(
        store,
        "Turn off DreamView",
        {
            "text": "DreamView is off.",
            "_dialogue_entities": ["DreamView"],
            "verification_status": "verified",
        },
    )
    _observe(store, "There is no device called DreamView", {"text": "Got it."})

    resolution = store.resolve_references(
        "Turn it on",
        platform="telegram",
        owner_id="owner",
        history=[
            ("user", "Turn off DreamView"),
            ("assistant", "DreamView is off."),
            ("user", "There is no device called DreamView"),
        ],
    )
    assert resolution.used_context is False
    assert resolution.resolved_text == "Turn it on"


def test_both_uses_latest_verified_device_set_not_unrelated_nouns():
    store = DialogueStateStore()
    _observe(
        store,
        "Turn off all lights",
        {
            "text": "Both lights are off.",
            "_dialogue_entities": ["AI Sync Box strip", "Floor Lamp 2"],
            "verification_status": "verified",
        },
    )
    _observe(store, "Thanks, the work project can wait", {"text": "Of course."})

    resolution = store.resolve_references(
        "Turn both devices on", platform="telegram", owner_id="owner"
    )
    assert resolution.resolved_text == ("Turn AI Sync Box strip and Floor Lamp 2 on")


def test_yes_is_approval_only_when_approval_is_pending():
    empty = classify_transition("yes")
    assert empty.kind is not TransitionClass.APPROVAL

    store = DialogueStateStore()
    _observe(
        store,
        "Delete the report",
        {
            "text": "Should I delete it?",
            "execution_status": "waiting_approval",
            "approval_required": True,
        },
    )
    pending = store.transition_for("yes", platform="telegram", owner_id="owner")
    assert pending.kind is TransitionClass.APPROVAL


def test_fresh_request_interrupts_pending_task_and_ignores_proactive_context():
    store = DialogueStateStore()
    _observe(
        store,
        "Research batteries",
        {"text": "Working on it.", "execution_status": "running"},
    )
    assert store.record_proactive(
        platform="telegram",
        owner_id="owner",
        message_id="proactive-1",
        topic="batteries",
    )

    transition = store.transition_for(
        "Write a birthday note", platform="telegram", owner_id="owner"
    )
    assert transition.kind is TransitionClass.INTERRUPTION


def test_unanswered_proactive_messages_cannot_stack():
    store = DialogueStateStore()
    assert store.record_proactive(
        platform="telegram", owner_id="owner", message_id="one"
    )
    assert not store.record_proactive(
        platform="telegram", owner_id="owner", message_id="two"
    )


def test_rejected_topic_is_scoped_and_does_not_block_a_different_project():
    store = DialogueStateStore()
    rejection = store.transition_for(
        "Stop talking about the compiler project",
        platform="telegram",
        owner_id="owner",
    )
    _observe(
        store,
        "Stop talking about the compiler project",
        {"text": "Understood."},
        transition=rejection,
    )

    new_project = store.transition_for(
        "Help me design the garden project",
        platform="telegram",
        owner_id="owner",
    )
    snapshot = store.snapshot(platform="telegram", owner_id="owner")
    assert new_project.kind is TransitionClass.NEW_GOAL
    assert "compiler project" in snapshot.topic_rejections.value


def test_context_budget_never_truncates_current_request_or_atomic_tool_result():
    budgeter = ContextBudgeter(
        total_budget=512,
        section_budgets={
            ContextSection.TOOL_RESULT: 24,
            ContextSection.RECENT_VERBATIM: 24,
        },
    )
    current = "current " * 500
    receipt = "verified receipt " * 200
    envelope = budgeter.assemble(
        [
            ContextCandidate(
                "current",
                ContextSection.CURRENT_MESSAGE,
                current,
                "current request",
            ),
            ContextCandidate(
                "receipt",
                ContextSection.TOOL_RESULT,
                receipt,
                "consequential evidence",
                consequential=True,
            ),
        ]
    )

    assert envelope.text(ContextSection.CURRENT_MESSAGE) == current
    assert envelope.text(ContextSection.TOOL_RESULT) == receipt
    assert not any(item.truncated for item in envelope.decisions)
    assert envelope.unavoidable_overflow_tokens > 0


def test_context_budget_excludes_rejected_and_contradicted_items_with_reasons():
    envelope = ContextBudgeter(total_budget=512).assemble(
        [
            ContextCandidate(
                "current",
                ContextSection.CURRENT_MESSAGE,
                "Discuss the garden",
                "current request",
            ),
            ContextCandidate(
                "rejected",
                ContextSection.DURABLE_MEMORY,
                "compiler project",
                "topic match",
                rejected=True,
            ),
            ContextCandidate(
                "contradicted",
                ContextSection.DURABLE_MEMORY,
                "old device name",
                "memory match",
                contradicted=True,
            ),
        ]
    )

    excluded = [item for item in envelope.decisions if not item.included]
    assert len(excluded) == 2
    assert {item.candidate_id for item in excluded} == {
        "rejected",
        "contradicted",
    }
    assert all(item.reason.startswith("excluded:") for item in excluded)


def test_prompt_context_excludes_memory_for_an_explicitly_rejected_topic():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    rejection = workflow.dialogue_state.transition_for(
        "Stop talking about the compiler project",
        platform="telegram",
        owner_id="owner",
    )
    _observe(
        workflow.dialogue_state,
        "Stop talking about the compiler project",
        {"text": "Understood."},
        transition=rejection,
    )
    analysis = analyze_turn(
        "How should I arrange the garden project?",
        "How should I arrange the garden project?",
        owner_id="owner",
        platform="telegram",
    )
    recalled = [
        {
            "key": "compiler project",
            "value": "old optimization work",
            "status": "verified",
            "confidence": 0.9,
            "_retrieval_reason": "weak shared word",
        }
    ]
    with (
        patch("memory.adaptive.get_relevant_memories", return_value=recalled),
        patch("memory.adaptive.get_matching_abilities", return_value=[]),
        patch("memory.adaptive.get_pending_memory_conflicts", return_value=[]),
    ):
        envelope, _, memories, _, _ = workflow._prepare_prompt_context(
            [],
            "How should I arrange the garden project?",
            internal_id="owner",
            platform="telegram",
            turn_analysis=analysis,
            transition=workflow.dialogue_state.transition_for(
                "How should I arrange the garden project?",
                platform="telegram",
                owner_id="owner",
            ),
        )

    assert memories == []
    memory_decision = next(
        item for item in envelope.decisions if item.candidate_id == "memory-0"
    )
    assert not memory_decision.included
    assert "explicitly rejected" in memory_decision.reason


def test_self_contained_device_commands_do_not_retrieve_memory():
    assert not ContextBudgeter.memory_retrieval_allowed(
        "operational_minimal", requires_reference=False
    )
    assert ContextBudgeter.memory_retrieval_allowed(
        "operational_minimal", requires_reference=True
    )


def test_rolling_summary_corrections_rewrite_conflicting_inferences():
    history = [("user", "There is no device called DreamView")]
    corrections = extract_explicit_corrections(
        history, lambda entry: f"{entry[0]}:{entry[1]}"
    )
    rewritten = rewrite_summary_conflicts(
        "DreamView is the user's television. The garden plan remains active.",
        corrections,
    )

    assert "television" not in rewritten
    assert "garden plan remains active" in rewritten
    assert "Explicit correction" in rewritten


def test_rolling_summary_records_deterministic_source_range_and_inference_label():
    class Metadata:
        def __init__(self):
            self.value = {}

        def get_metadata(self, _platform, _owner):
            return dict(self.value)

        def set_metadata(self, _platform, _owner, key, value):
            self.value[key] = value

    metadata = Metadata()
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    workflow._HISTORY_SUMMARISE_THRESHOLD = 4
    workflow._HISTORY_KEEP_RECENT = 2
    history = [
        ("user" if index % 2 == 0 else "assistant", f"turn {index}")
        for index in range(8)
    ]
    with (
        patch("agent.chat_workflow.get_session_manager", return_value=metadata),
        patch("llm.providers.ask_best_provider", return_value="The goal is active."),
    ):
        compacted = workflow._maybe_summarise_history(
            history, platform="telegram", internal_id="owner"
        )

    record = metadata.value["working_context_v1"]
    assert record["record_schema"] == 2
    assert record["generated_inference"] is True
    assert record["source_turn_range"]["count"] == 6
    assert record["source_turn_range"]["start"]
    assert record["source_turn_range"]["end"]
    assert "model-generated inference" in compacted[0][1]
