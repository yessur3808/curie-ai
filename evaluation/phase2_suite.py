"""Deterministic Phase 2 dialogue-state and context-isolation release gate."""

from __future__ import annotations

import json

from agent.kernel.dialogue_state import DialogueStateStore, TransitionClass
from agent.kernel.understanding import analyze_turn


def _observe(
    store: DialogueStateStore,
    text: str,
    result: dict,
    *,
    transition=None,
) -> None:
    analysis = analyze_turn(
        text,
        text,
        owner_id="evaluation",
        platform="telegram",
    )
    transition = transition or store.transition_for(
        text, platform="telegram", owner_id="evaluation"
    )
    store.observe_for_owner(
        "evaluation",
        analysis.state,
        result,
        text=text,
        transition=transition,
    )


def _device_state() -> DialogueStateStore:
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
    return store


def _evaluate() -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []

    store = _device_state()
    work = store.transition_for(
        "Help me outline the work presentation",
        platform="telegram",
        owner_id="evaluation",
    )
    results.append(("device_to_work_topic", work.kind is TransitionClass.NEW_GOAL))

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
    correction = store.resolve_references(
        "Turn it on",
        platform="telegram",
        owner_id="evaluation",
        history=[
            ("user", "Turn off DreamView"),
            ("assistant", "DreamView is off."),
            ("user", "There is no device called DreamView"),
        ],
    )
    results.append(
        (
            "nonexistent_device_correction",
            not correction.used_context and correction.resolved_text == "Turn it on",
        )
    )

    store = _device_state()
    plural = store.resolve_references(
        "Turn both devices on", platform="telegram", owner_id="evaluation"
    )
    results.append(
        (
            "both_after_two_devices",
            plural.resolved_text == "Turn AI Sync Box strip and Floor Lamp 2 on",
        )
    )

    store = _device_state()
    _observe(store, "Thanks", {"text": "Anytime."})
    singular = store.resolve_references(
        "Turn it on", platform="telegram", owner_id="evaluation"
    )
    results.append(
        (
            "it_after_social_checkin",
            singular.resolved_text == "Turn AI Sync Box strip on",
        )
    )

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
    approval = store.transition_for("yes", platform="telegram", owner_id="evaluation")
    results.append(
        ("yes_with_pending_approval", approval.kind is TransitionClass.APPROVAL)
    )

    store = DialogueStateStore()
    _observe(
        store,
        "Research batteries",
        {"text": "Working on it.", "execution_status": "running"},
    )
    interruption = store.transition_for(
        "Write a birthday note",
        platform="telegram",
        owner_id="evaluation",
    )
    results.append(
        (
            "new_request_while_task_pending",
            interruption.kind is TransitionClass.INTERRUPTION,
        )
    )

    store = DialogueStateStore()
    rejection = store.transition_for(
        "Stop talking about the compiler project",
        platform="telegram",
        owner_id="evaluation",
    )
    _observe(
        store,
        "Stop talking about the compiler project",
        {"text": "Understood."},
        transition=rejection,
    )
    other_project = store.transition_for(
        "Help me design the garden project",
        platform="telegram",
        owner_id="evaluation",
    )
    snapshot = store.snapshot(platform="telegram", owner_id="evaluation")
    results.append(
        (
            "rejected_topic_does_not_poison_other_project",
            other_project.kind is TransitionClass.NEW_GOAL
            and "compiler project" in snapshot.topic_rejections.value,
        )
    )
    return results


def main() -> int:
    results = _evaluate()
    for case_id, passed in results:
        print(f"{'PASS' if passed else 'FAIL'} {case_id}")
    passed = sum(passed for _, passed in results)
    score = round(passed / len(results) * 10, 2)
    print(
        json.dumps({"total": len(results), "passed": passed, "score": score}, indent=2)
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
