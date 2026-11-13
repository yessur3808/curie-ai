"""Curie-owned voice storage and runtimes; metadata checks never load models."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class VoicePaths:
    root: Path
    home: Path
    speech_python: Path
    transcription_python: Path
    voice_id: str = "curie"

    @classmethod
    def from_root(cls, root: Path, voice_id: str | None = None):
        root = root.resolve()
        selected = re.sub(r"[^a-z0-9_-]+", "-", (voice_id or "curie").casefold()).strip(
            "-"
        )
        selected = selected or "curie"
        env_suffix = re.sub(r"[^A-Z0-9]+", "_", selected.upper())
        if selected in {"curie", "default"}:
            home = Path(os.environ.get("CURIE_VOICE_HOME", root / "data/trained-voice"))
        else:
            home = Path(
                os.environ.get(
                    f"CURIE_VOICE_HOME_{env_suffix}",
                    root / "data/trained-voices" / selected,
                )
            )
        return cls(
            root,
            home,
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
            selected,
        )

    @property
    def config(self):
        return self.home / "config.json"

    def ready(self):
        try:
            cfg = json.loads(self.config.read_text())
            return bool(
                cfg.get("enabled")
                and cfg.get("engine") in {"chatterbox-nano", "chatterbox-multilingual"}
                and not (
                    cfg.get("engine") == "chatterbox-multilingual"
                    and cfg.get("adapter")
                )
                and self.speech_python.is_file()
                and (self.home / cfg["profile"]).is_file()
                and (not cfg.get("adapter") or (self.home / cfg["adapter"]).is_file())
                and all(
                    (self.home / cfg["model"] / name).is_file()
                    for name in (
                        (
                            cfg.get("t3Model", "t3_mtl23ls_v2.safetensors"),
                            "s3gen.pt",
                            "ve.pt",
                            "grapheme_mtl_merged_expanded_v1.json",
                        )
                        if cfg.get("engine") == "chatterbox-multilingual"
                        else (
                            "t3_nano_v1.safetensors",
                            "s3gen_meanflow.safetensors",
                            "ve.safetensors",
                            "tokenizer_config.json",
                        )
                    )
                )
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
