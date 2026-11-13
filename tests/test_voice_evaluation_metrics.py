import array
import math
import wave

import pytest

from services.trained_voice.evaluation_metrics import distance, wav_metrics, words


def test_bus_numbers_and_times_keep_values_but_ignore_digit_spelling():
    assert words("18X at 3:30") == words("eighteen X at three thirty")
    assert distance(words("18X in 8 minutes"), words("33X in 13 minutes")) > 0
    assert words("100") != words("200")


def test_french_accents_and_apostrophes_are_not_deleted():
    assert words("Voilà, c’est intéressant.") == ["voilà", "c'est", "intéressant"]
    assert words("Voilà") == words("Voila\u0300")
    assert words("café") != words("cafe")


def test_word_errors_distinguish_omissions_and_repetitions():
    assert distance(words("I can check that"), words("I can can check that")) == 1
    assert distance(words("I can check that"), words("I check that")) == 1


def test_acoustic_diagnostics_measure_duration_clipping_and_silence(tmp_path):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(array.array("h", [0] * 8000 + [32767] * 8000).tobytes())
    metrics = wav_metrics(path)
    assert metrics["durationSeconds"] == 1
    assert metrics["clippedSampleFraction"] == 0.5
    assert metrics["quietSampleFraction"] == 0.5
    assert math.isfinite(metrics["peak"])


def test_empty_audio_is_not_a_successful_evaluation(tmp_path):
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
    with pytest.raises(ValueError, match="nonempty"):
        wav_metrics(path)
