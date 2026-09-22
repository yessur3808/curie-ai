import asyncio
import json

import pytest

from agent.kernel.errors import PipelineError, PipelineErrorKind
from agent.observability import TRACE_REQUIRED_FIELDS, TurnEventWriter
from agent.slo import SLOMetrics
from evaluation.failure_drills import failure_drill_report
from evaluation.phase10_hardening_suite import run as run_phase10
from scripts.scan_runtime_artifacts import scan_paths
from services.backpressure import BackpressureRejected, RuntimeBackpressure
from utils.redaction import REDACTED, redact_secrets, secret_markers


class _TraceState:
    def as_dict(self):
        return {
            "trace_id": "trace-1",
            "turn_id": "turn-1",
            "platform": "telegram",
            "owner_scope_hash": "owner-hash",
            "message_hash": "message-hash",
            "message_chars": 24,
            "mode": "active",
            "failed_stage": "execute",
            "stages": [
                {
                    "stage": "normalize",
                    "status": "completed",
                    "duration_ms": 1.0,
                    "summary": {},
                },
                {
                    "stage": "understand",
                    "status": "completed",
                    "duration_ms": 2.0,
                    "summary": {
                        "intent": "device_control",
                        "recognizer_confidence": 0.97,
                    },
                },
                {
                    "stage": "route",
                    "status": "completed",
                    "duration_ms": 1.5,
                    "summary": {
                        "route": "tool",
                        "capabilities": ["home_control"],
                    },
                },
                {
                    "stage": "execute",
                    "status": "failed",
                    "duration_ms": 8.0,
                    "summary": {"model_family": "deterministic"},
                },
                {
                    "stage": "verify",
                    "status": "degraded",
                    "duration_ms": 1.0,
                    "summary": {"verification_status": "unverified"},
                },
                {
                    "stage": "plan_response",
                    "status": "completed",
                    "duration_ms": 1.0,
                    "summary": {"outcome": "failed"},
                },
            ],
        }


def test_structured_trace_assigns_failure_stage_and_never_writes_message_or_secret(
    tmp_path,
):
    path = tmp_path / "turn-events.jsonl"
    writer = TurnEventWriter(path)
    raw_secret = "123456789:" + "S" * 24
    writer.record_pipeline(
        _TraceState(),
        response={
            "text": "private user message " + raw_secret,
            "model_used": "local-model",
            "response_origin": "pipeline",
            "telemetry": {
                "prompt_tokens": 20,
                "output_tokens": 8,
                "queue_wait_ms": 3.5,
            },
        },
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert TRACE_REQUIRED_FIELDS <= payload.keys()
    assert payload["stage"] == "execute"
    assert payload["pipeline_stage_statuses"]["execute"] == "failed"
    assert payload["route"] == "tool"
    assert payload["capability"] == "home_control"
    assert payload["verification_category"] == "unverified"
    assert payload["token_counts"] == {"prompt": 20, "output": 8}
    serialized = json.dumps(payload)
    assert "private user message" not in serialized
    assert raw_secret not in serialized
    assert not secret_markers(serialized)


def test_slo_dashboard_separates_connector_model_tool_and_verification_delays():
    metrics = SLOMetrics()
    metrics.observe("connector_receive", 10)
    metrics.observe("model_generation", 120)
    metrics.observe("tool_execution", 45)
    metrics.observe("verification", 8)
    snapshot = metrics.snapshot()
    assert snapshot["connector_receive"]["p95_ms"] == 10
    assert snapshot["model_generation"]["p95_ms"] == 120
    assert snapshot["tool_execution"]["p95_ms"] == 45
    assert snapshot["verification"]["p95_ms"] == 8
    assert snapshot["connector_delivery"]["status"] == "no_data"


@pytest.mark.asyncio
async def test_backpressure_rejects_excess_reads_and_only_same_owner_mutations():
    pressure = RuntimeBackpressure(global_read_limit=1, per_owner_mutation_limit=1)
    async with pressure.tool_slot(owner_id="a", mutating=False):
        with pytest.raises(BackpressureRejected, match="global_read"):
            async with pressure.tool_slot(owner_id="b", mutating=False):
                pass
    async with pressure.tool_slot(owner_id="a", mutating=True):
        with pytest.raises(BackpressureRejected, match="owner_mutation"):
            async with pressure.tool_slot(owner_id="a", mutating=True):
                pass
        async with pressure.tool_slot(owner_id="b", mutating=True):
            await asyncio.sleep(0)
    snapshot = pressure.snapshot()
    assert snapshot["global_reads"]["rejected"] == 1
    assert snapshot["owner_mutations"]["rejected"] == 1
    assert snapshot["saturated"] is False


def test_recursive_redaction_covers_structured_fields_urls_headers_and_artifacts(
    tmp_path,
):
    telegram = "123456789:" + "T" * 24
    raw = {
        "bot_token": telegram,
        "callback": "https://provider.invalid/cb?code=" + "C" * 24,
        "headers": {"Authorization": "Bearer " + "B" * 24},
        "database_url": "".join(
            (
                "post",
                "gresql",
                ":",
                "/" * 2,
                "curie:",
                "P" * 24,
                "@db.invalid/curie",
            )
        ),
        "artifact": "access_token=" + "A" * 24,
        "latency": {"first_token": 12.5, "token_counts": {"output": 8}},
    }
    redacted = redact_secrets(raw)
    text = json.dumps(redacted, sort_keys=True)
    assert REDACTED in text
    assert telegram not in text
    assert redacted["latency"] == {
        "first_token": 12.5,
        "token_counts": {"output": 8},
    }
    assert not secret_markers(text)
    artifact = tmp_path / "report.json"
    artifact.write_text(text, encoding="utf-8")
    assert scan_paths([artifact])["passed"]

    unsafe = tmp_path / "unsafe.log"
    unsafe.write_text("Authorization: Bearer " + "U" * 24, encoding="utf-8")
    scan = scan_paths([unsafe])
    assert scan["passed"] is False
    assert scan["findings"][0]["markers"] == ["provider_header"]

    structured = tmp_path / "structured.json"
    structured.write_text(
        json.dumps({"client_secret": "short-but-private"}), encoding="utf-8"
    )
    scan = scan_paths([structured])
    assert scan["passed"] is False
    assert scan["findings"][0]["markers"] == ["structured_secret"]


def test_pipeline_error_categories_support_required_recovery_paths():
    assert (
        PipelineError.from_exception("execute", RuntimeError("model unavailable")).kind
        is PipelineErrorKind.UNAVAILABLE_CAPABILITY
    )
    assert (
        PipelineError.from_exception(
            "execute", PermissionError("OAuth authorization expired")
        ).kind
        is PipelineErrorKind.AUTHENTICATION_EXPIRED
    )
    assert (
        PipelineError.from_exception("verify", RuntimeError("state mismatch")).kind
        is PipelineErrorKind.VERIFICATION_FAILED
    )


def test_all_required_failure_drills_capture_privacy_safe_recovery_evidence():
    report = failure_drill_report()
    assert report["all_passed"]
    assert report["passed"] == report["total"] == 12
    assert {item["drill"] for item in report["drills"]} == {
        "model_unavailable",
        "database_locked",
        "corrupt_memory_database",
        "telegram_reconnect",
        "smart_home_timeout",
        "expired_oauth",
        "disk_full",
        "queue_saturation",
        "process_restart_during_task",
        "duplicate_inbound",
        "credential_compromise",
        "emergency_stop",
    }
    assert not secret_markers(json.dumps(report, sort_keys=True))


def test_phase10_hardening_suite_passes_every_blocking_metric():
    report = run_phase10()
    assert all(report["checks"].values())
    assert set(report["metrics"].values()) == {1.0}
