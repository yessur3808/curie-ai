import asyncio

from agent.kernel.planning import PlanExecutor, build_execution_plan
from agent.kernel.understanding import analyze_turn
from agent.orchestration.contracts import ResponseCandidate


def _analysis(text: str):
    return analyze_turn(
        text,
        text,
        owner_id="owner",
        platform="telegram",
    )


def test_independent_read_only_requests_are_planned_in_parallel():
    analysis = _analysis("Check RAM usage and check hardware specs")
    plan = build_execution_plan(
        analysis.state,
        analysis.operational_decisions,
        preserve_order=analysis.preserve_order,
    )

    assert [step.capability for step in plan.steps] == ["ram_usage", "hardware"]
    assert plan.execution_mode == "parallel_read_only"
    assert all(not step.depends_on for step in plan.steps)


def test_explicit_sequence_preserves_dependencies():
    analysis = _analysis("Check RAM usage and then check hardware specs")
    plan = build_execution_plan(
        analysis.state,
        analysis.operational_decisions,
        preserve_order=analysis.preserve_order,
    )

    assert plan.execution_mode == "sequential"
    assert plan.steps[1].depends_on == (plan.steps[0].id,)


def test_multiple_mutations_joined_by_and_remain_sequential():
    analysis = _analysis("Turn off DreamView and turn on the desk lamp")
    plan = build_execution_plan(
        analysis.state,
        analysis.operational_decisions,
        preserve_order=analysis.preserve_order,
    )

    assert [step.capability for step in plan.steps] == [
        "home_control",
        "home_control",
    ]
    assert plan.execution_mode == "sequential"
    assert plan.steps[1].depends_on == (plan.steps[0].id,)


def test_parallel_executor_keeps_user_requested_result_order():
    analysis = _analysis("Check RAM usage and check hardware specs")
    plan = build_execution_plan(
        analysis.state,
        analysis.operational_decisions,
        preserve_order=analysis.preserve_order,
    )
    both_started = asyncio.Event()
    started = 0

    async def execute(decision):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return ResponseCandidate(
            decision.selected_capability,
            f"test:{decision.selected_capability}",
            {"status": "completed", "verification_status": "not_required"},
        )

    result = asyncio.run(PlanExecutor().execute(plan, execute))

    assert result.status == "completed"
    assert result.message_parts == ["ram_usage", "hardware"]


def test_sequential_executor_preserves_partial_results_and_skips_dependents():
    analysis = _analysis(
        "Check RAM usage then also check hardware specs then also measure current network speed and latency"
    )
    plan = build_execution_plan(
        analysis.state,
        analysis.operational_decisions,
        preserve_order=analysis.preserve_order,
    )

    async def execute(decision):
        if decision.selected_capability == "hardware":
            raise RuntimeError("hardware unavailable")
        return ResponseCandidate(
            f"completed {decision.selected_capability}",
            f"test:{decision.selected_capability}",
            {"status": "completed", "verification_status": "not_required"},
        )

    result = asyncio.run(PlanExecutor().execute(plan, execute))

    assert result.status == "partial"
    assert [outcome.status for outcome in result.outcomes] == [
        "completed",
        "failed",
        "skipped_dependency",
    ]
    assert "completed ram_usage" in result.text
    assert "earlier step failed" in result.text
