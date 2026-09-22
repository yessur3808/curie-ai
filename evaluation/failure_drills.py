"""Deterministic, offline recovery drills for Phase 10 operations."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any, Callable

from agent.kernel.cancellation import CancellationRegistry
from agent.kernel.errors import PipelineError, PipelineErrorKind
from agent.task_runtime import DurableTaskRuntime
from connectors.lifecycle import ConnectorApplication, ConnectorState
from evaluation.tool_provider_simulator import ProviderMode, simulate_provider
from services.backpressure import BackpressureRejected, RuntimeBackpressure
from services.runtime_health import disk_budget
from utils.dedupe import DedupeCache
from utils.redaction import redact_secrets, secret_markers


@dataclass(frozen=True, slots=True)
class DrillEvidence:
    drill: str
    failure: str
    stage: str
    recovery: str
    passed: bool
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _evidence(
    drill: str,
    failure: str,
    stage: str,
    recovery: str,
    passed: bool,
    **details: Any,
) -> DrillEvidence:
    return DrillEvidence(
        drill=drill,
        failure=failure,
        stage=stage,
        recovery=recovery,
        passed=bool(passed),
        evidence=redact_secrets(details),
    )


def _model_unavailable() -> DrillEvidence:
    error = PipelineError.from_exception("execute", RuntimeError("model unavailable"))
    passed = (
        error.kind is PipelineErrorKind.UNAVAILABLE_CAPABILITY
        and error.retryable
        and error.stage == "execute"
    )
    return _evidence(
        "model_unavailable",
        "inference provider cannot serve the request",
        "execute",
        "use the bounded fallback path and retry only when capacity returns",
        passed,
        error=error.as_dict(),
    )


def _database_locked() -> DrillEvidence:
    with TemporaryDirectory(prefix="curie-db-lock-") as directory:
        path = Path(directory) / "memory.sqlite3"
        primary = sqlite3.connect(path, timeout=0)
        contender = sqlite3.connect(path, timeout=0)
        recovered = False
        detected = False
        try:
            primary.execute("CREATE TABLE probe (value TEXT)")
            primary.commit()
            primary.execute("BEGIN EXCLUSIVE")
            primary.execute("INSERT INTO probe VALUES ('held')")
            try:
                contender.execute("INSERT INTO probe VALUES ('contender')")
                contender.commit()
            except sqlite3.OperationalError as exc:
                detected = "locked" in str(exc).casefold()
                contender.rollback()
            primary.rollback()
            contender.execute("INSERT INTO probe VALUES ('recovered')")
            contender.commit()
            recovered = (
                contender.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 1
            )
        finally:
            contender.close()
            primary.close()
    return _evidence(
        "database_locked",
        "SQLite writer lock",
        "persistence",
        "fail the attempt without corrupting state, then retry after the lock clears",
        detected and recovered,
        lock_detected=detected,
        retry_succeeded=recovered,
    )


def _corrupt_memory_database() -> DrillEvidence:
    with TemporaryDirectory(prefix="curie-db-corrupt-") as directory:
        path = Path(directory) / "memory.sqlite3"
        path.write_bytes(b"not a sqlite database")
        detected = False
        try:
            with sqlite3.connect(path) as connection:
                connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError:
            detected = True
        path.unlink()
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE recovery (value TEXT)")
            recovered = connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    return _evidence(
        "corrupt_memory_database",
        "memory database fails integrity check",
        "persistence",
        "quarantine the corrupt file and rebuild a checked store before accepting writes",
        detected and recovered,
        corruption_detected=detected,
        replacement_integrity_ok=recovered,
    )


def _telegram_reconnect() -> DrillEvidence:
    probes = iter((False, True))
    connector = ConnectorApplication(
        "telegram",
        lambda _workflow: None,
        ready_probe=lambda: next(probes, True),
    )
    connector.state = ConnectorState.STARTING
    first = connector.refresh_readiness()
    second = connector.wait_ready(timeout=0.1)
    return _evidence(
        "telegram_reconnect",
        "Telegram readiness probe drops during reconnect",
        "connector_receive",
        "hold delivery until a fresh readiness probe succeeds",
        not first and second and connector.state is ConnectorState.READY,
        initial_ready=first,
        recovered_ready=second,
        final_state=connector.state.value,
    )


def _smart_home_timeout() -> DrillEvidence:
    result = simulate_provider(ProviderMode.TIMEOUT)
    passed = (
        result.execution_status == "failed"
        and result.verification_status == "unverified"
        and result.error_type == "timeout"
    )
    return _evidence(
        "smart_home_timeout",
        "device provider times out after a mutation attempt",
        "verification",
        "report the result as unverified and never claim the device changed",
        passed,
        observation=result.as_dict(),
    )


def _expired_oauth() -> DrillEvidence:
    error = PipelineError.from_exception(
        "execute", PermissionError("OAuth authorization expired")
    )
    passed = error.kind is PipelineErrorKind.AUTHENTICATION_EXPIRED
    return _evidence(
        "expired_oauth",
        "provider authorization is expired",
        "execute",
        "request reauthorization and do not retry with the expired credential",
        passed,
        error=error.as_dict(),
    )


def _disk_full() -> DrillEvidence:
    status = disk_budget(0, 2 * 1024**3)
    return _evidence(
        "disk_full",
        "free disk falls below the write safety budget",
        "persistence",
        "degrade readiness and reject new durable writes until space is restored",
        status["ready"] is False,
        health=status,
    )


async def _saturate_queue() -> tuple[bool, dict[str, Any]]:
    pressure = RuntimeBackpressure(global_read_limit=1, per_owner_mutation_limit=1)
    read_rejected = False
    mutation_rejected = False
    async with pressure.tool_slot(owner_id="owner-a", mutating=False):
        try:
            async with pressure.tool_slot(owner_id="owner-b", mutating=False):
                pass
        except BackpressureRejected:
            read_rejected = True
    async with pressure.tool_slot(owner_id="owner-a", mutating=True):
        try:
            async with pressure.tool_slot(owner_id="owner-a", mutating=True):
                pass
        except BackpressureRejected:
            mutation_rejected = True
        async with pressure.tool_slot(owner_id="owner-b", mutating=True):
            pass
    snapshot = pressure.snapshot()
    return read_rejected and mutation_rejected, snapshot


def _queue_saturation() -> DrillEvidence:
    passed, snapshot = asyncio.run(_saturate_queue())
    return _evidence(
        "queue_saturation",
        "read and per-owner mutation budgets are exhausted",
        "execute",
        "reject excess work promptly while allowing another owner's mutation",
        passed,
        backpressure=snapshot,
    )


def _process_restart() -> DrillEvidence:
    mutation = DurableTaskRuntime.interrupted_step_action("mutating")
    read = DurableTaskRuntime.interrupted_step_action("read_only")
    return _evidence(
        "process_restart_during_task",
        "the process restarts with an interrupted task step",
        "execute",
        "verify mutations without replay; safely retry read-only work",
        mutation == "verify_without_replay" and read == "retry_read",
        mutation_action=mutation,
        read_action=read,
    )


def _duplicate_inbound() -> DrillEvidence:
    cache = DedupeCache(ttl_seconds=60, max_size=10)
    first = cache.check("telegram:update:1", now=100.0)
    duplicate = cache.check("telegram:update:1", now=101.0)
    return _evidence(
        "duplicate_inbound",
        "the connector delivers the same inbound event twice",
        "connector_receive",
        "accept the first event and suppress the duplicate before tool execution",
        first is False and duplicate is True and cache.size() == 1,
        first_was_duplicate=first,
        replay_was_duplicate=duplicate,
    )


def _credential_compromise() -> DrillEvidence:
    telegram = "123456789:" + "A" * 24
    oauth = "https://provider.invalid/callback?code=" + "C" * 20 + "&state=opaque"
    database = "".join(
        ("post", "gresql", ":", "/" * 2, "curie:", "P" * 20, "@db.invalid/curie")
    )
    raw = {
        "bot_token": telegram,
        "authorization": "Bearer " + "B" * 24,
        "callback": oauth,
        "database_url": database,
    }
    redacted = redact_secrets(raw)
    serialized = json.dumps(redacted, sort_keys=True)
    no_raw_values = all(value not in serialized for value in raw.values())
    passed = no_raw_values and not secret_markers(serialized)
    return _evidence(
        "credential_compromise",
        "credential-shaped data reaches a diagnostic artifact",
        "observability",
        "redact structured fields, headers, OAuth URLs, bot tokens, and DB URLs",
        passed,
        detector_markers=list(secret_markers(serialized)),
        artifact=redacted,
    )


async def _cancel_active_task() -> tuple[int, bool]:
    registry = CancellationRegistry()
    event = asyncio.Event()
    registry.register("owner-a", event)
    cancelled = registry.cancel("owner-a")
    return cancelled, event.is_set()


def _emergency_stop() -> DrillEvidence:
    cancelled, event_set = asyncio.run(_cancel_active_task())
    return _evidence(
        "emergency_stop",
        "an owner requests immediate cancellation",
        "execute",
        "signal every active owner-scoped plan and prevent its next mutation",
        cancelled == 1 and event_set,
        signalled_tasks=cancelled,
        cancellation_event_set=event_set,
    )


_DRILLS: tuple[Callable[[], DrillEvidence], ...] = (
    _model_unavailable,
    _database_locked,
    _corrupt_memory_database,
    _telegram_reconnect,
    _smart_home_timeout,
    _expired_oauth,
    _disk_full,
    _queue_saturation,
    _process_restart,
    _duplicate_inbound,
    _credential_compromise,
    _emergency_stop,
)


def run_failure_drills() -> tuple[DrillEvidence, ...]:
    """Run all offline drills without touching production state or the network."""
    return tuple(drill() for drill in _DRILLS)


def failure_drill_report() -> dict[str, Any]:
    drills = run_failure_drills()
    return redact_secrets(
        {
            "schema_version": 1,
            "offline": True,
            "passed": sum(item.passed for item in drills),
            "total": len(drills),
            "all_passed": all(item.passed for item in drills),
            "drills": [item.as_dict() for item in drills],
        }
    )


__all__ = ["DrillEvidence", "failure_drill_report", "run_failure_drills"]
