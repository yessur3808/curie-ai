#!/usr/bin/env python3
"""ASR is an intelligibility check, not a measure of accent authenticity."""

import argparse
import collections
import json
import pathlib
import re


def words(text):
    text = text.lower().replace("’", "'").replace("%", " percent")
    for source, target in {
        "i'm": "i am",
        "let's": "let us",
        "you're": "you are",
        "that's": "that is",
    }.items():
        text = text.replace(source, target)
    text = re.sub(r"\b3[:.]30\b", "three thirty", text)
    text = re.sub(r"\b12\b", "twelve", text)
    text = re.sub(r"\byas(?:er|ir|sir|ser)\b", "yaser", text)
    return re.findall(r"[a-z]+", text)


def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(prev[j] + 1, current[-1] + 1, prev[j - 1] + (x != y)))
        prev = current
    return prev[-1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
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
            language="en",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        transcript = " ".join(s.text.strip() for s in segments)
        ref = words(row["text"])
        errors = distance(ref, words(transcript))
        row.update(
            transcript=transcript,
            wordErrors=errors,
            wordCount=len(ref),
            wer=errors / max(1, len(ref)),
        )
        summary = scores[row["variant"]]
        summary["errors"] += errors
        summary["words"] += len(ref)
        summary["audioSeconds"] += row["seconds"]
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
        score["wer"] = score["errors"] / score["words"]
    (root / "scores.json").write_text(
        json.dumps(
            {
                "samples": rows,
                "summary": scores,
                "limitation": "ASR estimates intelligibility; a human listening comparison is needed for accent and naturalness.",
            },
            indent=2,
        )
    )
    print(json.dumps(scores), flush=True)
