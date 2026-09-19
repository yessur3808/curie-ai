"""Consent-gated local multilingual custom voice inference."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import threading

logger = logging.getLogger(__name__)
os.environ.setdefault("MPLCONFIGDIR", "/tmp/curie-matplotlib")
_model = None
_lock = threading.Lock()


def custom_voice_health(config: dict | None = None) -> dict:
    config = config or {}
    model = Path(os.getenv("XTTS_MODEL_PATH", ""))
    model_config = Path(os.getenv("XTTS_CONFIG_PATH", ""))
    reference = Path(str(config.get("custom_voice_reference", "")))
    consent = config.get("custom_voice_consent") is True
    try:
        import TTS  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    return {
        "ready": bool(
            consent
            and installed
            and model.is_file()
            and model_config.is_file()
            and reference.is_file()
        ),
        "consented": consent,
        "engine_installed": installed,
        "model_configured": model.is_file() and model_config.is_file(),
        "reference_configured": reference.is_file(),
        "local_only": True,
    }


def _load_model():
    global _model
    if _model is not None:
        return _model
    model_path = os.getenv("XTTS_MODEL_PATH", "").strip()
    config_path = os.getenv("XTTS_CONFIG_PATH", "").strip()
    if not Path(model_path).is_file() or not Path(config_path).is_file():
        return None
    with _lock:
        if _model is None:
            from TTS.api import TTS

            _model = TTS(model_path=model_path, config_path=config_path, gpu=False)
    return _model


async def synthesize_custom_voice(text: str, output_path: str, config: dict) -> bool:
    """Synthesize locally only when consent, reference, and model are present."""
    health = custom_voice_health(config)
    if not health["ready"]:
        logger.warning("Custom voice requested but not ready: %s", health)
        return False
    reference = str(config["custom_voice_reference"])

    def generate() -> None:
        model = _load_model()
        if model is None:
            raise RuntimeError("XTTS model is not configured")
        model.tts_to_file(
            text=text,
            speaker_wav=[reference],
            language="en",
            file_path=output_path,
            split_sentences=True,
        )

    try:
        await asyncio.to_thread(generate)
        target = Path(output_path)
        return target.is_file() and target.stat().st_size > 0
    except Exception as exc:
        logger.warning("Custom voice synthesis failed: %s", exc)
        Path(output_path).unlink(missing_ok=True)
        return False
