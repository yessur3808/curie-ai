"""Diagnostic speech metrics; these do not measure identity or naturalness."""

import array
import math
import re
import sys
import unicodedata
import wave

_SMALL = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}


def words(text):
    text = unicodedata.normalize("NFC", text).lower().replace("’", "'")
    text = text.replace("%", " percent")
    for source, target in {
        "i'm": "i am",
        "let's": "let us",
        "you're": "you are",
        "that's": "that is",
    }.items():
        text = text.replace(source, target)

    # ASR may spell bus numbers and clock times as digits. Never discard digits:
    # mismatched times/quantities must remain visible in the error score.
    def number(match):
        raw = match.group()
        n = int(raw)
        if len(raw) > 1 and raw.startswith("0"):
            return " " + " ".join(_SMALL[int(x)] for x in raw) + " "
        if n < 20:
            return " " + _SMALL[n] + " "
        if n < 60:
            return " " + _TENS[n // 10] + (" " + _SMALL[n % 10] if n % 10 else "") + " "
        return " " + raw + " "

    text = re.sub(r"\d+", number, text)
    # Unicode words retain French accents and apostrophes rather than dropping them.
    return re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)*|\d+", text)


def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(prev[j] + 1, current[-1] + 1, prev[j - 1] + (x != y)))
        prev = current
    return prev[-1]


def wav_metrics(path):
    """Inspect bounded PCM16 WAV output; silence is an energy proxy only."""
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("Expected uncompressed PCM16 WAV")
        rate, channels, count = (
            audio.getframerate(),
            audio.getnchannels(),
            audio.getnframes(),
        )
        if not count or count > rate * 300:
            raise ValueError(
                "Evaluation audio must be nonempty and at most five minutes"
            )
        samples = array.array("h", audio.readframes(count))
    if sys.byteorder != "little":
        samples.byteswap()
    size = max(1, round(rate * 0.02)) * channels
    quiet = total = 0
    for i in range(0, len(samples), size):
        frame = samples[i : i + size]
        rms = math.sqrt(sum(x * x for x in frame) / len(frame)) / 32768
        total += len(frame)
        if rms < 0.005:
            quiet += len(frame)
    return {
        "durationSeconds": count / rate,
        "sampleRate": rate,
        "peak": max(abs(x) for x in samples) / 32768,
        "clippedSampleFraction": sum(abs(x) >= 32760 for x in samples) / len(samples),
        "quietSampleFraction": quiet / total,
    }
