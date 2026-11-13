#!/usr/bin/env python3
"""Generate identical private prompts across base/reference/adapter variants."""

import argparse
import json
import os
import pathlib
import time

PROMPTS = [
    "Bonjour, Yaser. I'm here. Let's see what needs your attention today.",
    "Your next meeting is at three thirty. The latest deployment is healthy, and traffic is up twelve percent.",
    "There is a thunderstorm warning in Hong Kong. Please take care if you're going outside.",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompts",
        help="JSON list of text/category/language records for a fixed comparison set",
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--old-profile", required=True)
    parser.add_argument("--strengths", default="")
    parser.add_argument("--output-name", default="evaluation")
    parser.add_argument("--checkpoint", default="run/best.safetensors")
    args = parser.parse_args()
    prompts = (
        json.loads(pathlib.Path(args.prompts).read_text())
        if args.prompts
        else [
            {"text": text, "language": "en", "category": "legacy"} for text in PROMPTS
        ]
    )
    if not prompts or not all(
        isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()
        for p in prompts
    ):
        parser.error("--prompts must contain nonempty text records")
    os.environ.update(
        OMP_NUM_THREADS="6",
        MKL_NUM_THREADS="6",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    os.nice(10)
    import gc
    import torch
    import soundfile as sf
    from chatterbox.tts_turbo import ChatterboxTurboTTS, Conditionals
    from .voice_lora import merge

    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    root = pathlib.Path(args.root)
    out = root / args.output_name
    out.mkdir(exist_ok=True)
    results = []
    variants = [
        ("before", 0, pathlib.Path(args.old_profile)),
        ("reference-only", 0, root / "curie-profile-v2.pt"),
        ("trained", 1, root / "curie-profile-v2.pt"),
        ("trained-subtle", 0.65, root / "curie-profile-v2.pt"),
    ]
    if args.strengths:
        variants = [
            (
                "trained-" + str(round(float(s) * 100)),
                float(s),
                root / "curie-profile-v2.pt",
            )
            for s in args.strengths.split(",")
        ]
    for name, strength, profile in variants:
        model = ChatterboxTurboTTS.from_local(args.model, device="cpu", nano=True)
        if strength:
            merge(model.t3, root / args.checkpoint, strength)
        model.conds = Conditionals.load(profile, map_location="cpu").to("cpu")
        for index, item in enumerate(prompts):
            text = item["text"]
            torch.manual_seed(852)
            start = time.perf_counter()
            with torch.inference_mode():
                audio = (
                    model.generate(text, temperature=0.65, top_p=0.9).squeeze(0).numpy()
                )
            path = out / f"{name}-{index}.wav"
            sf.write(path, audio, model.sr, subtype="PCM_16")
            result = {
                "variant": name,
                "strength": strength,
                "text": text,
                "category": item.get("category", "unspecified"),
                "language": item.get("language", "en"),
                "audio": path.name,
                "seconds": len(audio) / model.sr,
                "generationSeconds": time.perf_counter() - start,
            }
            results.append(result)
            print(
                json.dumps({k: v for k, v in result.items() if k != "text"}), flush=True
            )
        del model
        gc.collect()
    (out / "samples.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
