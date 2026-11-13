import unittest
import numpy as np
from services.trained_voice.prosody import analyze_samples


class ProsodyTests(unittest.TestCase):
    def test_silence_clipping_noise_and_short_samples_are_unreliable(self):
        for audio in [
            np.zeros(32000),
            np.ones(32000),
            np.ones(100),
            np.full(32000, np.nan),
            np.random.default_rng(42).normal(0, 0.05, 32000),
        ]:
            self.assertEqual(analyze_samples(audio)["status"], "unreliable")

    def test_delivery_features_do_not_claim_feelings(self):
        t = np.arange(48000) / 16000
        quiet = 0.01 * np.sin(2 * np.pi * 170 * t)
        animated = 0.2 * np.sin(
            2 * np.pi * (160 * t + 35 / (2 * np.pi) * np.sin(2 * np.pi * t))
        )
        for signal, expected in [(quiet, "quiet"), (animated, "animated")]:
            result = analyze_samples(signal)
            self.assertEqual(result["delivery"], expected)
            self.assertEqual(result["confidence"], "low")
            self.assertNotIn("emotion", result)


if __name__ == "__main__":
    unittest.main()
