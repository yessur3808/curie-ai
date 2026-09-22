#!/usr/bin/env python3
"""CPU-bounded Nano LoRA fine-tuning with held-out causal speech-token loss.

Training contract follows production inference: separate same-speaker reference,
raw GPT-2 text, speech-start then target, and next-token labels ending at stop.
See https://github.com/Pseud0naut/chatterbox-nano-language-training for the
independently documented Nano contract and pitfalls in upstream T3.loss().
"""

import argparse
import json
import math
import os
import pathlib
import random
import resource
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--steps", type=int, default=240)
    parser.add_argument("--learning-rate", type=float, default=0.0001)
    parser.add_argument("--threads", type=int, default=6)
    args = parser.parse_args()
    os.environ.update(
        OMP_NUM_THREADS=str(args.threads),
        MKL_NUM_THREADS=str(args.threads),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    os.nice(10)
    import torch
    import torch.nn.functional as F
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    from chatterbox.models.t3.modules.cond_enc import T3Cond
    from .voice_lora import attach, save

    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(852)
    rng = random.Random(852)
    root = pathlib.Path(args.root)
    manifest = json.loads((root / "features.json").read_text())
    if manifest["contract"] != 2:
        raise ValueError("Wrong feature contract")
    model = ChatterboxTurboTTS.from_local(args.model, device="cpu", nano=True)
    t3 = model.t3
    del model.s3gen, model.ve
    adapters = attach(t3)
    params = [p for p in t3.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=args.learning_rate, weight_decay=0.01)
    data = []
    for row in manifest["records"]:
        d = torch.load(root / row["feature"], map_location="cpu", weights_only=True)
        if d["contract"] != 2 or d["id"] == d["conditioningID"]:
            raise ValueError("Leaking conditioning target")
        if (d["target"] >= 6561).any() or len(d["target"]) < 5:
            raise ValueError("Invalid speech targets")
        data.append((row, d))
    curie = [d for r, d in data if r["split"] == "train" and r["source"] == "curie"]
    french = [
        d for r, d in data if r["split"] == "train" and r["source"] == "french-english"
    ]
    validation = [d for r, d in data if r["split"] == "validation"]
    if len(curie) < 50 or len(validation) < 5 or not french:
        raise ValueError("Insufficient training or validation data")

    def loss(d):
        cond = T3Cond(
            speaker_emb=d["speaker"].clone(),
            cond_prompt_speech_tokens=d["prompt"].unsqueeze(0),
            emotion_adv=None,
        )
        c = t3.prepare_conditioning(cond)
        target = d["target"].long()
        speech = torch.cat((torch.tensor([6561]), target))
        labels = torch.cat((target, torch.tensor([6562])))
        embeddings = torch.cat(
            (
                c,
                t3.text_emb(d["text"].unsqueeze(0)),
                t3.speech_emb(speech.unsqueeze(0)),
            ),
            dim=1,
        )
        hidden = t3.tfmr(
            inputs_embeds=embeddings, use_cache=False, return_dict=True
        ).last_hidden_state[:, -len(speech) :]
        logits = t3.speech_head(hidden)
        return F.cross_entropy(logits.reshape(-1, logits.shape[-1]), labels)

    def validate():
        t3.eval()
        with torch.no_grad():
            result = sum(float(loss(d)) for d in validation) / len(validation)
        t3.train()
        return result

    out = root / "run"
    out.mkdir(exist_ok=True)
    started = time.perf_counter()
    baseline = validate()
    best = baseline
    history = [{"step": 0, "validationLoss": baseline}]
    report = {
        "method": "LoRA weight fine-tuning",
        "rank": 8,
        "alpha": 16,
        "contract": 2,
        "seed": 852,
        "trainableParameters": sum(p.numel() for p in params),
        "curieClips": len(curie),
        "frenchEnglishClips": len(french),
        "validationClips": len(validation),
        "learningRate": args.learning_rate,
        "history": history,
        "status": "training",
    }
    (out / "metrics.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(history[-1]), flush=True)
    queue = []
    recent = []
    optimizer.zero_grad(set_to_none=True)
    for step in range(1, args.steps + 1):
        for micro in range(2):
            if rng.random() < 0.15:
                d = rng.choice(french)
            else:
                if not queue:
                    queue = curie.copy()
                    rng.shuffle(queue)
                d = queue.pop()
            value = loss(d)
            if not torch.isfinite(value):
                raise ValueError("Nonfinite loss")
            (value / 2).backward()
            recent.append(float(value.detach()))
        torch.nn.utils.clip_grad_norm_(params, 1)
        warmup = min(1, step / 15)
        decay = 0.25 + 0.75 * (1 + math.cos(math.pi * step / args.steps)) / 2
        for group in optimizer.param_groups:
            group["lr"] = args.learning_rate * warmup * decay
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        if step % 20 == 0 or step == args.steps:
            log = {
                "step": step,
                "trainLoss": sum(recent) / len(recent),
                "seconds": round(time.perf_counter() - started, 1),
            }
            recent = []
            if step % 60 == 0 or step == args.steps:
                score = validate()
                log["validationLoss"] = score
                save(adapters, out / f"adapter-{step}.safetensors")
                if score < best:
                    best = score
                    save(adapters, out / "best.safetensors")
                    report["bestStep"] = step
            history.append(log)
            report.update(
                elapsedSeconds=round(time.perf_counter() - started, 1),
                bestValidationLoss=best,
                completedSteps=step,
                peakMiB=round(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                ),
            )
            (out / "metrics.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(log), flush=True)
    report["status"] = "complete"
    report["improvedHeldOutLoss"] = best < baseline
    (out / "metrics.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
