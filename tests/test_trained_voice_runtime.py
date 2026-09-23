"""Resource and fallback contracts for the shared trained-voice worker."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.trained_voice.runtime import (
    TrainedVoiceBusy,
    _acquire_file_lock,
    trained_voice_required,
)


class TrainedVoiceRuntimeTests(unittest.TestCase):
    def test_trained_voice_is_required_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(trained_voice_required())
            os.environ["CURIE_TRAINED_VOICE_REQUIRED"] = "false"
            self.assertFalse(trained_voice_required())

    def test_worker_lock_refuses_overlapping_model_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "trained-voice.lock"

            async def exercise():
                first = await _acquire_file_lock(lock, 0)
                try:
                    with self.assertRaises(TrainedVoiceBusy):
                        await _acquire_file_lock(lock, 0)
                finally:
                    if hasattr(first, "release"):
                        first.release()
                    else:
                        os.close(first)

            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
