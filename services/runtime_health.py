"""Independent capability readiness and graceful-degradation reporting."""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import shutil
import sqlite3


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
    database = _database_health()
    disk = shutil.disk_usage(Path.cwd())
    minimum = int(os.getenv("CURIE_MIN_FREE_DISK_BYTES", str(2 * 1024**3)))
    disk_health = {
        "ready": disk.free >= minimum,
        "free_bytes": disk.free,
        "minimum_free_bytes": minimum,
        "percent_used": round(disk.used / disk.total * 100, 1),
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
        "database": database,
        "disk": disk_health,
        "security": security_status(),
        "inference": get_inference_service().snapshot(),
    }
    required = (capabilities["text"]["ready"], database["ready"], disk_health["ready"])
    return {
        "status": "healthy" if all(required) else "degraded",
        "capabilities": capabilities,
    }


def handle_health_command(text: str, workflow_ready: bool = True) -> str | None:
    if text.strip().casefold() not in {"/health", "/readiness"}:
        return None
    health = capability_health(workflow_ready)
    capabilities = health["capabilities"]
    labels = []
    for name in ("text", "vision", "transcription", "speech", "database", "disk"):
        item = capabilities[name]
        labels.append(f"{name}={'ready' if item.get('ready') else 'degraded'}")
    return f"Curie is {health['status']}. " + ", ".join(labels) + "."
