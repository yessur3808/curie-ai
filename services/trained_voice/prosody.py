"""Local acoustic delivery cues, never a diagnosis or a claimed emotion detector."""


def analyze_samples(audio, sample_rate=16000):
    import numpy as np

    y = np.asarray(audio, dtype=np.float32).reshape(-1)[: sample_rate * 30]
    unknown = {"status": "unreliable", "delivery": "unknown", "confidence": "low"}
    if len(y) < sample_rate or not np.isfinite(y).all():
        return unknown
    clipping = float(np.mean(np.abs(y) >= 0.995))
    frame, hop = int(0.04 * sample_rate), int(0.02 * sample_rate)
    frames = np.lib.stride_tricks.sliding_window_view(y, frame)[::hop]
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    peak = float(np.max(rms))
    if peak < 0.003 or clipping > 0.02:
        return unknown
    selected = frames[rms > max(0.003, peak * 0.15)]
    if len(selected) * 0.02 < 0.6:
        return unknown
    pitches = []
    lo, hi = int(sample_rate / 450), int(sample_rate / 75)
    for row in selected[::2]:
        row = row - row.mean()
        corr = np.correlate(row, row, "full")[frame - 1 :]
        lag = lo + int(np.argmax(corr[lo:hi]))
        if corr[0] > 0 and corr[lag] / corr[0] > 0.6:
            pitches.append(sample_rate / lag)
    if len(pitches) < 10:
        return unknown
    spread = 0.0
    if len(pitches) >= 10:
        spread = float(
            12 * np.log2(np.percentile(pitches, 90) / np.percentile(pitches, 10))
        )
    db = float(
        20 * np.log10(max(float(np.median(rms[rms > max(0.003, peak * 0.15)])), 1e-6))
    )
    # Absolute loudness depends on microphone gain. Use only weak delivery cues,
    # no pitch/gender norms and no positive/negative emotion classification.
    delivery = (
        "animated" if spread >= 7 and db > -28 else "quiet" if db < -32 else "steady"
    )
    return {
        "status": "available",
        "delivery": delivery,
        "confidence": "low",
        "voicedSeconds": round(len(selected) * 0.02, 2),
        "pitchVariationSemitones": round(spread, 2),
        "levelDbfs": round(db, 2),
        "clippingFraction": round(clipping, 4),
    }


def analyze_file(path):
    from .affect import enabled

    if not enabled():
        return {"status": "disabled", "delivery": "unknown", "confidence": "low"}
    try:
        from faster_whisper.audio import decode_audio

        return analyze_samples(decode_audio(path, sampling_rate=16000))
    except Exception:
        return {"status": "unavailable", "delivery": "unknown", "confidence": "low"}
