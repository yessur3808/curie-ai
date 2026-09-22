"""Independent capability readiness and graceful-degradation reporting."""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import shutil
import sqlite3


def disk_budget(free_bytes: int, minimum_free_bytes: int) -> dict:
    minimum = max(1, int(minimum_free_bytes))
    free = max(0, int(free_bytes))
    return {
        "ready": free >= minimum,
        "free_bytes": free,
        "minimum_free_bytes": minimum,
    }


def _configured_file(name: str) -> dict:
    value = os.getenv(name, "").strip()
    return {"configured": bool(value), "ready": bool(value and Path(value).is_file())}


def _database_health() -> dict:
    from memory import local_store

    try:
        with sqlite3.connect(local_store._PATH, timeout=3) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()[0]
        return {"ready": result == "ok", "backend": "sqlite", "integrity": result}
    except Exception as exc:
        return {"ready": False, "backend": "sqlite", "error": str(exc)[:200]}


def capability_health(workflow_ready: bool = True) -> dict:
    """Return truthful, independently actionable capability readiness."""
    from llm import manager
    from llm.inference_service import get_inference_service
    from agent.kernel.feature_flags import PipelineFeatureFlags
    from agent.slo import slo_metrics
    from agent.understanding.entities import device_resolver_status
    from connectors.delivery_gateway import connector_gateway_status
    from memory import memory_kernel_status
    from services.backpressure import runtime_backpressure
    from services.media_ingestion import media_backpressure_snapshot
    from services.media_transport import media_transport_status
    from services.security import security_status
    from services.voice_delivery import voice_health

    models = []
    for value in os.getenv("LLM_MODELS", "").split(","):
        value = value.strip()
        if value:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = Path("models") / candidate
            models.append({"name": Path(value).name, "available": candidate.is_file()})
    text_ready = bool(manager.llama_models_cache) or any(
        item["available"] for item in models
    )
    vision_model = _configured_file("VISION_MODEL_PATH")
    vision_projector = _configured_file("VISION_MMPROJ_PATH")
    voice = voice_health()
    memory_kernel = memory_kernel_status()
    memory_kernel["ready"] = not (
        memory_kernel["mode"] == "rust" and memory_kernel["active"] != "rust"
    )
    media_transport = media_transport_status()
    media_transport["ready"] = not (
        media_transport["mode"] == "rust" and media_transport["active"] != "rust"
    )
    connector_gateway = connector_gateway_status()
    connector_gateway["ready"] = not (
        connector_gateway["mode"] == "rust" and connector_gateway["active"] != "rust"
    )
    device_resolver = device_resolver_status()
    device_resolver["ready"] = not (
        device_resolver["mode"] == "rust" and device_resolver["active"] != "rust"
    )
    database = _database_health()
    disk = shutil.disk_usage(Path.cwd())
    minimum = int(os.getenv("CURIE_MIN_FREE_DISK_BYTES", str(2 * 1024**3)))
    disk_health = {
        **disk_budget(disk.free, minimum),
        "percent_used": round(disk.used / disk.total * 100, 1),
    }
    inference = get_inference_service().snapshot()
    media_pressure = media_backpressure_snapshot()
    tool_pressure = runtime_backpressure.snapshot()
    backpressure = {
        "model": inference,
        "attachments": media_pressure,
        "tools": tool_pressure,
        "saturated": bool(
            inference["saturated"]
            or media_pressure["saturated"]
            or tool_pressure["saturated"]
        ),
    }
    transcription_ready = bool(importlib.util.find_spec("whisper"))
    capabilities = {
        "text": {
            "ready": text_ready and workflow_ready,
            "warm": bool(manager.llama_models_cache),
            "models": models,
            "fallback": "clear unavailable response",
        },
        "vision": {
            "ready": vision_model["ready"] and vision_projector["ready"],
            "model": vision_model,
            "projector": vision_projector,
            "fallback": "request text description",
        },
        "transcription": {
            "ready": transcription_ready,
            "fallback": "request typed text",
        },
        "speech": {**voice, "fallback": "complete text reply"},
        "media_transport": media_transport,
        "memory_kernel": memory_kernel,
        "connector_gateway": connector_gateway,
        "device_resolver": device_resolver,
        "database": database,
        "disk": disk_health,
        "security": security_status(),
        "inference": inference,
        "backpressure": backpressure,
        "slos": slo_metrics.snapshot(),
        "pipeline_rollout": PipelineFeatureFlags.from_env().status(),
    }
    required = (
        capabilities["text"]["ready"],
        database["ready"],
        disk_health["ready"],
        media_transport["ready"],
        memory_kernel["ready"],
        connector_gateway["ready"],
        device_resolver["ready"],
        not backpressure["saturated"],
    )
    return {
        "status": "healthy" if all(required) else "degraded",
        "capabilities": capabilities,
    }


def handle_health_command(text: str, workflow_ready: bool = True) -> str | None:
    if text.strip().casefold() not in {"/health", "/readiness"}:
        return None
    health = capability_health(workflow_ready)
    capabilities = health["capabilities"]
    lines = [f"**Curie status: {health['status'].title()}**", ""]
    for name in (
        "text",
        "vision",
        "transcription",
        "speech",
        "media_transport",
        "memory_kernel",
        "connector_gateway",
        "device_resolver",
        "database",
        "disk",
    ):
        item = capabilities[name]
        ready = bool(item.get("ready"))
        icon = "✅" if ready else "⚠️"
        state = "Ready" if ready else "Degraded"
        label = name.replace("_", " ").title()
        lines.append(f"- {icon} **{label}:** {state}")
    capacity_ready = not capabilities["backpressure"]["saturated"]
    lines.append(
        "- "
        f"{'✅' if capacity_ready else '⚠️'} **Capacity:** "
        f"{'Available' if capacity_ready else 'Saturated'}"
    )
    rollout = capabilities["pipeline_rollout"]
    rollback = rollout["rollback"]
    lines.append(
        "- "
        f"{'⚠️' if rollback['active'] else '✅'} **Pipeline:** "
        f"{rollout['effective_stage'].replace('_', ' ').title()}"
        f"{' (rollback active)' if rollback['active'] else ''}"
    )
    return "\n".join(lines)
