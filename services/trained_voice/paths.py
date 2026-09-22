"""Curie-owned voice storage and runtimes; metadata checks never load models."""

from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class VoicePaths:
    root: Path
    home: Path
    speech_python: Path
    transcription_python: Path

    @classmethod
    def from_root(cls, root: Path):
        root = root.resolve()
        return cls(
            root,
            Path(os.environ.get("CURIE_VOICE_HOME", root / "data/trained-voice")),
            Path(
                os.environ.get(
                    "CURIE_VOICE_PYTHON", root / ".trained-voice-venv/bin/python"
                )
            ),
            Path(
                os.environ.get(
                    "CURIE_TRANSCRIBE_PYTHON", root / ".transcription-venv/bin/python"
                )
            ),
        )

    @property
    def config(self):
        return self.home / "config.json"

    def ready(self):
        try:
            cfg = json.loads(self.config.read_text())
            return bool(
                cfg.get("enabled")
                and cfg.get("engine") == "chatterbox-nano"
                and self.speech_python.is_file()
                and (self.home / cfg["profile"]).is_file()
                and (not cfg.get("adapter") or (self.home / cfg["adapter"]).is_file())
                and all(
                    (self.home / cfg["model"] / name).is_file()
                    for name in (
                        "t3_nano_v1.safetensors",
                        "s3gen_meanflow.safetensors",
                        "ve.safetensors",
                        "tokenizer_config.json",
                    )
                )
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
