from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agent.orchestration.response_policy import ResponsePolicy
from agent.response_composer import (
    DetailLevel,
    OutcomeType,
    build_response_plan,
    choose_detail_level,
    render_outcome_template,
    render_response,
)
from memory import local_store
from memory.service import (
    ConfirmationState,
    MemoryService,
    MemoryTier,
    SQLiteMemoryRepository,
    get_memory_service,
)
from services.proactive_policy import rank_candidates


def _service(tmp_path, monkeypatch) -> MemoryService:
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    get_memory_service(reset=True)
    return MemoryService(SQLiteMemoryRepository())


def test_response_plan_selects_detail_independently_from_personality():
    assert (
        choose_detail_level("Turn off the lamp", response_mode="command_ack")
        is DetailLevel.TERSE
    )
    assert (
        choose_detail_level("Explain it thoroughly", response_mode="brief")
        is DetailLevel.DETAILED
    )
    assert (
        choose_detail_level(
            "What happened?",
            response_mode="brief",
            outcome=OutcomeType.PARTIAL_FAILURE,
        )
        is DetailLevel.STANDARD
    )


def test_renderer_preserves_verified_facts_and_message_boundaries():
    result = {
        "text": "Done. Both lights are off.",
        "verification_status": "verified",
        "execution": {
            "status": "completed",
            "outcomes": [
                {"status": "verified", "device_id": "lamp-1"},
                {"status": "verified", "device_id": "lamp-2"},
            ],
        },
        "message_parts": ["Done. Both lights are off."],
    }
    plan = build_response_plan(
        result,
        user_text="Turn off both lights",
        response_mode="command_ack",
        connector="telegram",
    )

    rendered = render_response(plan, result)

    assert rendered["text"] == result["text"]
    assert rendered["verification_status"] == "verified"
    assert rendered["execution"] == result["execution"]
    assert rendered["response_plan"]["detail"] == "terse"
    assert rendered["response_plan"]["outcome"] == "verified_success"


def test_renderer_rejects_fact_changes_after_planning():
    result = {
        "text": "DreamView is already off.",
        "verification_status": "already_satisfied",
        "device_id": "dreamview-1",
    }
    plan = build_response_plan(
        result,
        user_text="Turn off DreamView",
        response_mode="command_ack",
        connector="telegram",
    )
    changed = {**result, "verification_status": "verified", "device_id": "other"}

    with pytest.raises(ValueError, match="facts changed"):
        render_response(plan, changed)


def test_typed_fallbacks_are_truthful_and_brief():
    result = {"verification_status": "already_satisfied"}
    plan = build_response_plan(
        result,
        user_text="Turn it off",
        response_mode="command_ack",
        connector="telegram",
    )

    assert render_outcome_template(plan) == "It’s already set that way."
    assert render_response(plan, result)["text"] == "It’s already set that way."


def test_curie_policy_removes_mechanical_french_address_suffix():
    class Passthrough:
        def apply_response_style(self, response, *_args, **_kwargs):
            return response

    policy = ResponsePolicy({"name": "Curie"}, Passthrough())

    assert policy.finalize("The lamp is already off, monsieur.", "Turn it off") == (
        "The lamp is already off."
    )


def test_proactive_candidates_reject_unsupported_claims_and_device_commands():
    ranked = rank_candidates(
        [
            {
                "kind": "routine",
                "topic": "mood",
                "reason": "Friendly check-in",
                "message": "I noticed your mood seems low.",
                "confidence": 0.9,
                "usefulness": 0.8,
            },
            {
                "kind": "routine",
                "topic": "turn off all lights",
                "reason": "Recent command",
                "confidence": 0.95,
                "usefulness": 0.8,
            },
            {
                "kind": "deadline",
                "topic": "project deadline",
                "reason": "A confirmed deadline is near",
                "message": "Your project deadline is tomorrow.",
                "confidence": 0.95,
                "urgency": 0.9,
                "usefulness": 0.9,
                "evidence": [
                    {
                        "source": "calendar",
                        "reference": "event-17",
                        "confidence": 1.0,
                    }
                ],
            },
        ],
        {},
    )

    assert [item["topic"] for item in ranked] == ["project deadline"]
    assert ranked[0]["evidence"][0]["reference"] == "event-17"


def test_unified_memory_record_has_full_provenance_and_owner_isolation(
    tmp_path, monkeypatch
):
    service = _service(tmp_path, monkeypatch)
    record = service.remember(
        "owner-a",
        predicate="favorite_food",
        value="spicy noodles",
        type="preference",
        source="explicit_user_statement",
        source_turn="turn-17",
        source_channel="telegram",
        evidence="I love spicy noodles",
    )

    assert record is not None
    assert record.tier is MemoryTier.CORE
    assert record.confirmation_state is ConfirmationState.CONFIRMED
    assert record.source_turn == "turn-17"
    assert record.retention_class == "standard"
    assert service.retrieve("owner-b", "What food do I love?").hits == ()
    result = service.retrieve("owner-a", "What food do I love?")
    assert [hit.record.value for hit in result.hits] == ["spicy noodles"]
    assert result.hits[0].record.retrieval_count == 1


def test_legacy_profile_facts_use_unified_retrieval_policy(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    supplemental = service.profile_records(
        "u1",
        {
            "favorite_food": "spicy noodles",
            "timezone": "Asia/Hong_Kong",
            "proactive_daily_max": 4,
        },
    )

    result = service.retrieve(
        "u1",
        "What food do I enjoy?",
        supplemental_records=supplemental,
    )

    assert [item.record.predicate for item in result.hits] == ["favorite_food"]
    assert all(item.predicate != "timezone" for item in supplemental)


def test_memory_write_policy_rejects_secrets_and_labels_inferences_candidates(
    tmp_path, monkeypatch
):
    service = _service(tmp_path, monkeypatch)
    assert (
        service.remember(
            "u1",
            predicate="password",
            value="correct horse battery staple",
            evidence="My password is private",
        )
        is None
    )
    candidate = service.remember(
        "u1",
        predicate="possible_routine",
        value="evening reading",
        type="routine",
        source="model_inference",
        evidence="Repeated but unconfirmed pattern",
    )
    assert candidate is not None
    assert candidate.confirmation_state is ConfirmationState.CANDIDATE
    assert candidate.valid_until is not None
    assert (
        service.remember(
            "u1",
            predicate="current_mood",
            value="sad",
            type="biography",
            source="model_inference",
            evidence="The model guessed an emotion",
        )
        is None
    )
    assert (
        service.remember(
            "u1",
            predicate="medical_detail",
            value="private fact",
            canonical_subject="third_party:friend",
            source="explicit_user_statement",
            evidence="Unrelated third-party information",
        )
        is None
    )


def test_conflicting_inferences_have_no_winner(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    first = service.remember(
        "u1",
        predicate="possible_schedule",
        value="morning",
        type="schedule",
        source="model_inference",
        evidence="Weak repeated pattern",
    )
    second = service.remember(
        "u1",
        predicate="possible_schedule",
        value="evening",
        type="schedule",
        source="model_inference",
        evidence="A conflicting repeated pattern",
    )

    assert first is not None and second is not None
    records = service.inspect("u1")
    assert {item.confirmation_state for item in records} == {
        ConfirmationState.CONFLICTED
    }
    assert all(item.active is False for item in records)
    assert service.retrieve("u1", "possible schedule", explicit_search=True).hits == ()


def test_stale_confirmed_fact_requires_reconfirmation_for_consequential_use(
    tmp_path, monkeypatch
):
    service = _service(tmp_path, monkeypatch)
    record = service.remember(
        "u1",
        predicate="shipping_address",
        value="Old address",
        type="biography",
        evidence="My shipping address is the old address",
    )
    assert record is not None
    service.repository.upsert(
        replace(
            record,
            updated_at=datetime.now(timezone.utc) - timedelta(days=365),
        )
    )

    result = service.retrieve(
        "u1",
        "shipping address",
        explicit_search=True,
        consequential=True,
    )

    assert result.hits == ()
    assert result.outcome == "reconfirmation_required"


def test_explicit_correction_invalidates_prior_fact_and_preserves_provenance(
    tmp_path, monkeypatch
):
    service = _service(tmp_path, monkeypatch)
    old = service.remember(
        "u1",
        predicate="favorite_drink",
        value="tea",
        type="preference",
        evidence="I prefer tea",
    )
    corrected = service.correct("u1", "favorite_drink", "coffee", source_turn="turn-2")

    assert old is not None and corrected is not None
    stored_old = service.repository.get("u1", old.record_id)
    assert stored_old is not None and stored_old.tombstone is True
    assert corrected.contradicted_record_ids == (old.record_id,)
    assert service.retrieve("u1", "favorite drink").hits[0].record.value == "coffee"
    provenance = service.explain_provenance("u1", corrected.record_id)
    assert provenance is not None
    assert provenance["source"] == "explicit_correction"
    assert provenance["source_turn"] == "turn-2"


def test_forget_is_tombstoned_and_absent_from_every_retrieval(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    record = service.remember(
        "u1",
        predicate="favorite_color",
        value="blue",
        type="preference",
        evidence="I like blue",
    )
    assert record is not None

    assert service.forget("u1", record_id=record.record_id) == 1
    assert service.retrieve("u1", "favorite color", explicit_search=True).hits == ()
    stored = service.inspect("u1", include_tombstones=True)
    assert stored[0].tombstone is True


def test_operational_memory_never_pages_in_as_a_personal_fact(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.remember(
        "u1",
        predicate="device_alias",
        value="DreamView maps to light.dreamview",
        type="device_alias",
        source="explicit_user_statement",
        evidence="Remember this device alias",
        tier=MemoryTier.OPERATIONAL,
    )

    assert service.retrieve("u1", "What do you know about DreamView?").hits == ()
    assert service.retrieve("u1", "Turn off DreamView").outcome == (
        "operational_bypass"
    )


def test_consolidation_is_dry_run_before_it_can_change_records(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    record = service.remember(
        "u1",
        predicate="trip",
        value="Paris",
        type="temporary_context",
        evidence="I am visiting Paris",
        valid_until=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert record is not None

    plan = service.consolidate("u1", dry_run=True)

    assert [item.action for item in plan.actions] == ["expire", "compact_index"]
    assert service.repository.get("u1", record.record_id).tombstone is False
    service.consolidate("u1", dry_run=False)
    assert service.repository.get("u1", record.record_id).tombstone is True


def test_legacy_migration_reconciles_counts_and_owner_without_loss(
    tmp_path, monkeypatch
):
    service = _service(tmp_path, monkeypatch)
    local_store.upsert_adaptive_memory(
        {
            "_id": "legacy-1",
            "id": "legacy-1",
            "owner_id": "u1",
            "internal_id": "u1",
            "key": "favorite_book",
            "value": "The Little Prince",
            "source": "explicit_user_statement",
        }
    )

    preview = service.migrate_owner("u1", dry_run=True)
    applied = service.migrate_owner("u1", dry_run=False)
    comparison = service.shadow_compare("u1")
    retrieval_comparison = service.shadow_compare_retrieval("u1", ["favorite book"])

    assert preview.source_count == 1
    assert preview.migrated_count == 0
    assert applied.migrated_count == 1
    assert applied.valid is True
    assert applied.rollback_until > datetime.now(timezone.utc)
    assert service.repository.cutover_mode("u1") == "unified"
    assert comparison["match"] is True
    assert retrieval_comparison["match"] is True
    inventory = service.inventory("u1", profile={"favorite_color": "blue"})
    assert inventory["legacy_record_count"] == 1
    assert inventory["unified_record_count"] == 1
    assert inventory["postgres_profile_fact_count"] == 1
    assert service.rollback_migration(applied) == len(applied.backup_documents)
    assert service.repository.cutover_mode("u1") == "legacy"
