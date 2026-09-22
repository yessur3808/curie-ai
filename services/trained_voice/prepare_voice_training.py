#!/usr/bin/env python3
"""Build private, auditable transcripts and independent speech-target features."""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess


def transcribe(root):
    from faster_whisper import WhisperModel

    model = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        num_workers=1,
        local_files_only=True,
    )
    records, rejected = [], []
    cache = root / "transcripts"
    cache.mkdir(exist_ok=True)
    prepared = root / "prepared"
    prepared.mkdir(exist_ok=True)
    for source in ("curie", "french-english"):
        manifest = json.loads((root / source / "manifest.json").read_text())
        for row in manifest["records"]:
            if source == "curie" and not 2.5 <= row["seconds"] <= 13:
                rejected.append(
                    {"id": row["id"], "reason": "duration outside 2.5–13 seconds"}
                )
                continue
            source_audio = root / source / row["audio"]
            transcript = cache / (row["id"] + ".json")
            if transcript.exists():
                segments = json.loads(transcript.read_text())
            else:
                result, _ = model.transcribe(
                    str(source_audio),
                    language="en",
                    beam_size=3,
                    vad_filter=True,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                )
                segments = [
                    {
                        "text": s.text.strip(),
                        "start": s.start,
                        "end": s.end,
                        "logprob": s.avg_logprob,
                        "noSpeech": s.no_speech_prob,
                        "compression": s.compression_ratio,
                        "words": [
                            {"word": w.word, "start": w.start, "end": w.end}
                            for w in s.words or []
                        ],
                    }
                    for s in result
                ]
                transcript.write_text(json.dumps(segments))
            if not segments or any(
                s["logprob"] < -0.85 or s["noSpeech"] > 0.6 or s["compression"] > 2.6
                for s in segments
            ):
                rejected.append({"id": row["id"], "reason": "low-confidence ASR"})
                continue
            if source == "curie":
                pieces = [
                    {
                        "text": " ".join(s["text"] for s in segments),
                        "start": 0,
                        "end": row["seconds"],
                    }
                ]
            else:
                pieces, words = [], []
                for word in [w for s in segments for w in s["words"]]:
                    if words and word["end"] - words[0]["start"] > 10:
                        pieces.append(
                            {
                                "text": "".join(w["word"] for w in words).strip(),
                                "start": max(0, words[0]["start"] - 0.08),
                                "end": words[-1]["end"] + 0.1,
                            }
                        )
                        words = []
                    words.append(word)
                if words:
                    pieces.append(
                        {
                            "text": "".join(w["word"] for w in words).strip(),
                            "start": max(0, words[0]["start"] - 0.08),
                            "end": words[-1]["end"] + 0.1,
                        }
                    )
            for index, piece in enumerate(pieces):
                if (
                    len(piece["text"].split()) < 4
                    or not 2.5 <= piece["end"] - piece["start"] <= 13
                ):
                    continue
                ident = row["id"] + (f"-{index}" if source != "curie" else "")
                wav = prepared / (ident + ".wav")
                if not wav.exists():
                    subprocess.run(
                        [
                            os.environ.get("FFMPEG_BIN", "ffmpeg"),
                            "-nostdin",
                            "-y",
                            "-loglevel",
                            "error",
                            "-ss",
                            str(piece["start"]),
                            "-i",
                            str(source_audio),
                            "-t",
                            str(piece["end"] - piece["start"]),
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            str(wav),
                        ],
                        check=True,
                    )
                records.append(
                    {
                        "id": ident,
                        "speaker": row["speaker"],
                        "audio": str(wav.relative_to(root)),
                        "text": piece["text"],
                        "seconds": piece["end"] - piece["start"],
                        "source": source,
                        "asrLogprob": min(s["logprob"] for s in segments),
                        "split": (
                            "validation"
                            if source == "curie"
                            and int(hashlib.sha256(ident.encode()).hexdigest()[:8], 16)
                            % 10
                            == 0
                            else "train"
                        ),
                    }
                )
            if len(records) % 20 == 0:
                print(
                    json.dumps(
                        {"transcribed": len(records), "rejected": len(rejected)}
                    ),
                    flush=True,
                )
    (root / "dataset.json").write_text(
        json.dumps(
            {
                "transcription": "faster-whisper base, English; confidence-filtered automatic transcripts",
                "records": records,
                "rejected": rejected,
            },
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "stage": "transcribed",
                "clips": len(records),
                "minutes": sum(r["seconds"] for r in records) / 60,
                "validation": sum(r["split"] == "validation" for r in records),
            }
        ),
        flush=True,
    )


def features(root, model_dir):
    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS, punc_norm

    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    model = ChatterboxTurboTTS.from_local(model_dir, device="cpu", nano=True)
    rows = json.loads((root / "dataset.json").read_text())["records"]
    output = root / "features"
    output.mkdir(exist_ok=True)
    pools = {}
    for r in rows:
        if r["split"] == "train" and 5.1 <= r["seconds"] <= 10.5:
            pools.setdefault(r["speaker"], []).append(r)
    conditioning, report = {}, []
    for speaker, candidates in pools.items():
        candidates.sort(key=lambda r: r["asrLogprob"], reverse=True)
        for r in candidates[:3]:
            audio, sr = sf.read(root / r["audio"], dtype="float32")
            with torch.inference_mode():
                embedding = torch.from_numpy(
                    model.ve.embeds_from_wavs([audio], sample_rate=sr)
                )
                prompt, lens = model.s3gen.tokenizer.forward(
                    [audio[: model.ENC_COND_LEN]],
                    max_len=model.t3.hp.speech_cond_prompt_len,
                )
                prompt = prompt.reshape(-1)[: int(lens.reshape(-1)[0])].clone()
            conditioning[r["id"]] = {
                "speaker": embedding.clone(),
                "prompt": prompt,
                "record": r,
            }
    with torch.inference_mode():
        for index, r in enumerate(rows):
            possible = [
                v
                for ident, v in conditioning.items()
                if v["record"]["speaker"] == r["speaker"] and ident != r["id"]
            ]
            if not possible:
                continue
            cond = possible[index % len(possible)]
            path = output / (r["id"] + ".pt")
            if not path.exists():
                audio, sr = sf.read(root / r["audio"], dtype="float32")
                if sr != 16000 or not np.isfinite(audio).all():
                    raise ValueError("Invalid prepared waveform")
                if (np.abs(audio) >= 0.999).mean() > 0.005 or np.sqrt(
                    np.mean(audio**2)
                ) < 0.003:
                    continue
                speech, lengths = model.s3gen.tokenizer.forward([audio])
                target = (
                    speech.reshape(-1)[: int(lengths.reshape(-1)[0])].clone().long()
                )
                text = (
                    model.tokenizer(punc_norm(r["text"]), return_tensors="pt")
                    .input_ids[0]
                    .clone()
                )
                if (
                    len(target) + len(text) + len(cond["prompt"]) + 2
                    > model.t3.tfmr.wpe.num_embeddings
                ):
                    continue
                torch.save(
                    {
                        "contract": 2,
                        "id": r["id"],
                        "conditioningID": cond["record"]["id"],
                        "speakerID": r["speaker"],
                        "text": text,
                        "target": target,
                        "speaker": cond["speaker"],
                        "prompt": cond["prompt"],
                        "split": r["split"],
                    },
                    path,
                )
            report.append(
                {
                    **r,
                    "feature": str(path.relative_to(root)),
                    "conditioningID": cond["record"]["id"],
                }
            )
            if len(report) % 25 == 0:
                print(
                    json.dumps({"features": len(report), "total": len(rows)}),
                    flush=True,
                )
    manifest = {
        "contract": 2,
        "records": report,
        "conditioningRecords": [v["record"] for v in conditioning.values()],
    }
    (root / "features.json").write_text(json.dumps(manifest, indent=2))
    print(
        json.dumps(
            {
                "stage": "features",
                "clips": len(report),
                "validation": sum(r["split"] == "validation" for r in report),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["transcribe", "features"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--model")
    args = parser.parse_args()
    os.environ.update(
        OMP_NUM_THREADS="6",
        MKL_NUM_THREADS="6",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    os.nice(10)
    if args.stage == "transcribe":
        transcribe(pathlib.Path(args.root))
    else:
        features(pathlib.Path(args.root), args.model)
