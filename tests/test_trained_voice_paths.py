"""Migration contracts: Curie owns paths and CLI tools remain model-lazy."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from services.trained_voice.paths import VoicePaths


class VoicePathTests(unittest.TestCase):
    def test_defaults_belong_to_curie_and_can_be_overridden(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            root = Path(directory).resolve()
            paths = VoicePaths.from_root(root)
            self.assertEqual(paths.config, root / "data/trained-voice/config.json")
            self.assertEqual(
                paths.speech_python, root / ".trained-voice-venv/bin/python"
            )
            self.assertEqual(
                paths.transcription_python, root / ".transcription-venv/bin/python"
            )
            self.assertFalse(paths.ready())
            with patch.dict(os.environ, {"CURIE_VOICE_HOME": str(root / "elsewhere")}):
                self.assertEqual(VoicePaths.from_root(root).home, root / "elsewhere")

    def test_readiness_requires_the_preserved_adapter_and_base_files(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            paths = VoicePaths.from_root(Path(directory))
            paths.home.mkdir(parents=True)
            paths.speech_python.parent.mkdir(parents=True)
            paths.speech_python.touch()
            config = {
                "enabled": True,
                "engine": "chatterbox-nano",
                "profile": "speaker.pt",
                "adapter": "adapter.safetensors",
                "model": "nano",
            }
            paths.config.write_text(json.dumps(config))
            (paths.home / "speaker.pt").touch()
            (paths.home / "adapter.safetensors").touch()
            (paths.home / "nano").mkdir()
            for name in (
                "t3_nano_v1.safetensors",
                "s3gen_meanflow.safetensors",
                "ve.safetensors",
                "tokenizer_config.json",
            ):
                (paths.home / "nano" / name).touch()
            self.assertTrue(paths.ready())
            (paths.home / "adapter.safetensors").unlink()
            self.assertFalse(paths.ready())
            paths.config.write_text("broken config")
            self.assertFalse(paths.ready())

    def test_packaged_worker_help_does_not_load_speech_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        for module in (
            "reference_voice",
            "train_voice_adapter",
            "prepare_reference_voice",
        ):
            result = subprocess.run(
                [sys.executable, "-m", "services.trained_voice." + module, "--help"],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
