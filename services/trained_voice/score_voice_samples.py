#!/usr/bin/env python3
"""ASR is an intelligibility check, not a measure of accent authenticity."""

import argparse
import collections
import json
import pathlib
from services.trained_voice.evaluation_metrics import distance, words, wav_metrics

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument(
        "--output",
        default="scores.json",
        help="Report filename inside the sample directory",
    )
    args = parser.parse_args()
    if pathlib.Path(args.output).name != args.output:
        parser.error("--output must be a filename")
    from faster_whisper import WhisperModel

    model = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        num_workers=1,
        local_files_only=True,
    )
    root = pathlib.Path(args.directory)
    rows = json.loads((root / "samples.json").read_text())
    scores = collections.defaultdict(
        lambda: {"errors": 0, "words": 0, "audioSeconds": 0, "generationSeconds": 0}
    )
    for row in rows:
        segments, _ = model.transcribe(
            str(root / row["audio"]),
            language=(
                None if row.get("language") == "auto" else row.get("language", "en")
            ),
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        transcript = " ".join(s.text.strip() for s in segments)
        ref = words(row["text"])
        acoustic = wav_metrics(root / row["audio"])
        errors = distance(ref, words(transcript))
        row.update(
            transcript=transcript,
            acoustic=acoustic,
            wordsPerMinute=round(len(ref) / acoustic["durationSeconds"] * 60, 1),
            wordErrors=errors,
            wordCount=len(ref),
            wer=errors / max(1, len(ref)),
        )
        summary = scores[row["variant"]]
        summary["errors"] += errors
        summary["words"] += len(ref)
        summary["audioSeconds"] += acoustic["durationSeconds"]
        summary["generationSeconds"] += row["generationSeconds"]
        print(
            json.dumps(
                {
                    "variant": row["variant"],
                    "audio": row["audio"],
                    "wer": row["wer"],
                    "transcript": transcript,
                }
            ),
            flush=True,
        )
    for score in scores.values():
        score["wer"] = score["errors"] / max(1, score["words"])
    (root / args.output).write_text(
        json.dumps(
            {
                "metricVersion": 2,
                "samples": rows,
                "summary": scores,
                "limitation": "ASR estimates intelligibility. Mixed-language recognition and spelling can affect scores; energy-based quiet/clipping and words-per-minute are diagnostic only. Human listening is required for naturalness, pronunciation and voice consistency. No automatic promotion.",
            },
            indent=2,
        )
    )
    print(json.dumps(scores), flush=True)
