"""Read-only, bounded view of the same operational data shown by Curie's dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def dashboard_snapshot() -> dict[str, Any]:
    from cli.dashboard import collect_instances
    from services.runtime_health import capability_health

    instances = []
    for row in collect_instances(sample=False):
        telemetry = row.get("telemetry") or {}
        latency = (telemetry.get("latency") or {}).get("total") or {}
        inference = telemetry.get("inference") or {}
        instances.append(
            {
                "instance": row.get("instance"),
                "running": bool(row.get("running")),
                "health": row.get("health"),
                "uptime": row.get("uptime"),
                "persona": row.get("persona"),
                "model": telemetry.get("last_model") or row.get("models"),
                "providers": row.get("providers"),
                "connectors": row.get("connectors"),
                "activity": row.get("activity"),
                "active_tasks": row.get("active_tasks"),
                "cpu": row.get("cpu"),
                "memory": row.get("memory"),
                "threads": row.get("threads"),
                "requests_per_minute": telemetry.get("requests_per_minute"),
                "latency_avg_ms": latency.get("avg_ms"),
                "latency_p95_ms": latency.get("p95_ms"),
                "first_token_ms": inference.get("first_token_ms"),
                "tokens_per_second": inference.get("tokens_per_second"),
                "queue_depth": inference.get(
                    "queue_depth", telemetry.get("queue_depth")
                ),
                "errors": max(
                    int(telemetry.get("errors", 0) or 0),
                    int(row.get("log_errors", 0) or 0),
                ),
                "fallbacks": telemetry.get("fallbacks"),
            }
        )
    health = capability_health(workflow_ready=True)
    try:
        from services.self_diagnostics import diagnostic_monitor

        diagnostics = diagnostic_monitor.trends(hours=24, bucket_minutes=30)
    except Exception:
        diagnostics = {"mode": "unknown", "points": []}
    readiness = {
        name: bool(item.get("ready"))
        for name, item in health.get("capabilities", {}).items()
        if isinstance(item, dict) and "ready" in item
    }
    running = [row for row in instances if row["running"]]
    overall = health.get("status")
    if not running:
        overall = "stopped"
    elif any(row["health"] == "degraded" for row in running):
        overall = "degraded"
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "instances": instances,
        "readiness": readiness,
        "diagnostics": diagnostics,
        "untrusted": False,
    }


__all__ = ["dashboard_snapshot"]
