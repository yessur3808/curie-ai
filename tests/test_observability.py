import time

from agent.observability import LatencyMetrics, OperationalMetrics, RequestTrace


def test_request_trace_records_stages_and_total():
    trace = RequestTrace()
    with trace.stage("tool"):
        time.sleep(0.001)
    timings = trace.finish()
    assert timings["tool"] > 0
    assert timings["total"] >= timings["tool"]


def test_latency_metrics_are_bounded_and_report_percentiles():
    metrics = LatencyMetrics(max_samples=2)
    metrics.observe({"total": 10})
    metrics.observe({"total": 20})
    metrics.observe({"total": 30})
    snapshot = metrics.snapshot()["total"]
    assert snapshot["count"] == 2
    assert snapshot["avg_ms"] == 25
    assert snapshot["p95_ms"] == 30


def test_operational_metrics_persist_privacy_safe_snapshot(tmp_path):
    metrics = OperationalMetrics(tmp_path / "telemetry.json")
    metrics.record_request(
        timings={"total": 250, "model_response": 200},
        prompt="private prompt text",
        response="private response text",
        model="model.gguf",
        role="reasoning",
        fallback=True,
        queue_depth=2,
    )

    import json

    snapshot = json.loads((tmp_path / "telemetry.json").read_text())
    assert snapshot["last_model"] == "model.gguf"
    assert snapshot["fallbacks"] == 1
    assert snapshot["queue_depth"] == 2
    assert "private prompt text" not in json.dumps(snapshot)
    assert "private response text" not in json.dumps(snapshot)
