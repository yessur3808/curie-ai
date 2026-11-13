#!/usr/bin/env python3
"""CPU speech synthesis shared by one-shot and bounded resident workers."""

import argparse
import json
import os
import pathlib
import resource
import sys
import time
import uuid

from .voice_text import speech_chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--delivery", default="{}")
    parser.add_argument(
        "--stream-dir", help="Publish completed WAV sentences here as JSON lines"
    )
    args = parser.parse_args()
    if hasattr(os, "nice"):
        os.nice(10)
    synthesize(args, sys.stdin.read(24001))


_MODEL = None
_MODEL_KEY = None
_TORCH_CONFIGURED = False
_INITIAL_RNG = None


def synthesize(args, text):
    global _MODEL, _MODEL_KEY, _TORCH_CONFIGURED, _INITIAL_RNG
    cfg_path = pathlib.Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text())
    delivery = json.loads(args.delivery)
    multilingual = cfg.get("engine") == "chatterbox-multilingual"
    temperature = max(
        0.4,
        min(
            0.85,
            float(delivery.get("temperature", cfg.get("temperature", 0.65)))
            + float(cfg.get("temperatureOffset", 0)),
        ),
    )
    pause = max(
        0.08,
        min(0.4, float(delivery.get("pause", 0.18)) * float(cfg.get("pauseScale", 1))),
    )
    if not text.strip() or len(text) > 24000:
        raise ValueError("Speech must contain 1–24,000 characters")
    # The adaptation corpus contains short dialogue. Keep production sequences
    # within that regime instead of joining several briefing sentences together.
    chunks = speech_chunks(
        text,
        limit=180 if cfg.get("adapter") else 240,
        sentence_only=bool(cfg.get("adapter")),
    )
    if multilingual:
        from .language_spans import language_spans

        chunks = [
            (lang, part)
            for lang, span in language_spans(text)
            for part in speech_chunks(span, limit=160, sentence_only=True)
        ]
    else:
        chunks = [("en", chunk) for chunk in chunks]
    threads = max(1, min(8, int(cfg.get("threads", 6))))
    os.environ.update(
        OMP_NUM_THREADS=str(threads),
        MKL_NUM_THREADS=str(threads),
        TOKENIZERS_PARALLELISM="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
    )
    started = time.perf_counter()
    import numpy as np
    import soundfile as sf
    import torch

    if multilingual:
        from chatterbox.mtl_tts import (
            ChatterboxMultilingualTTS as VoiceModel,
            Conditionals,
        )
    else:
        from chatterbox.tts_turbo import ChatterboxTurboTTS as VoiceModel, Conditionals

    torch.set_num_threads(threads)
    if not _TORCH_CONFIGURED:
        torch.set_num_interop_threads(1)
        _TORCH_CONFIGURED = True
    torch.manual_seed(852)
    assets = [cfg_path.parent / cfg["profile"]]
    if cfg.get("adapter"):
        assets.append(cfg_path.parent / cfg["adapter"])
    assets.extend((cfg_path.parent / cfg["model"]).glob("*.safetensors"))
    assets.extend((cfg_path.parent / cfg["model"]).glob("*.pt"))
    key = (
        str(cfg_path),
        json.dumps(cfg, sort_keys=True),
        tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in assets),
    )
    reused = _MODEL is not None and key == _MODEL_KEY
    if not reused:
        _MODEL = None
        model = VoiceModel.from_local(
            cfg_path.parent / cfg["model"],
            device="cpu",
            **(
                {"t3_model": cfg.get("t3Model", "t3_mtl23ls_v2.safetensors")}
                if multilingual
                else {"nano": True}
            ),
        )
        if cfg.get("adapter"):
            from .voice_lora import merge

            merge(
                model.t3,
                cfg_path.parent / cfg["adapter"],
                float(cfg.get("adapterStrength", 1.0)),
            )
        model.conds = Conditionals.load(
            cfg_path.parent / cfg["profile"], map_location="cpu"
        ).to("cpu")
        _MODEL, _MODEL_KEY = model, key
        _INITIAL_RNG = torch.get_rng_state()
    model = _MODEL
    torch.set_rng_state(_INITIAL_RNG)
    model_ready = time.perf_counter()
    first_chunk_seconds = None
    samples, retries = 0, 0
    # Write each chunk directly; long briefings do not accumulate tensors in RAM.
    with sf.SoundFile(
        args.output, mode="w", samplerate=model.sr, channels=1, subtype="PCM_16"
    ) as out:
        with torch.inference_mode():
            for index, (language, chunk) in enumerate(chunks):
                for attempt in range(2):
                    if attempt:
                        # Retry the same words and trained voice with a new seed.
                        # The bridge's overall timeout still bounds this work.
                        torch.manual_seed(1852 + index)
                        retries += 1
                    spoken_chunk = chunk
                    generation = {"temperature": temperature, "top_p": 0.9}
                    if multilingual:
                        # Native language tokens and real emotion conditioning.
                        spoken_chunk = chunk
                        expression = delivery.get("expression", "warm")
                        generation.update(
                            language_id=language,
                            cfg_weight=0.3,
                            exaggeration={
                                "bright": 0.8,
                                "gentle": 0.6,
                                "warm": 0.65,
                                "clear": 0.4,
                                "neutral": 0.5,
                            }.get(expression, 0.5),
                        )
                    wav = model.generate(spoken_chunk, **generation).squeeze(0).numpy()
                    valid = np.isfinite(wav).all() and model.sr // 4 <= len(
                        wav
                    ) <= model.sr * max(12, len(chunk.split()) * 0.85 + 3)
                    if valid:
                        break
                if not valid:
                    raise ValueError(
                        f"Invalid or possibly repeating audio at chunk {index}; refused after one retry"
                    )
                # A gentle, pitch-preserving cadence adjustment is shared by
                # streamed and ordinary speech. Persona rate remains separate.
                if abs(float(cfg.get("rateScale", 1)) - 1) > 0.005:
                    import librosa

                    wav = librosa.effects.time_stretch(
                        wav, rate=max(0.9, min(1.1, float(cfg["rateScale"])))
                    )
                persona_rate = float(delivery.get("rate", 1.0))
                persona_rate = max(0.85, min(1.15, persona_rate))
                if abs(persona_rate - 1.0) > 0.005:
                    import librosa

                    wav = librosa.effects.time_stretch(wav, rate=persona_rate)
                if first_chunk_seconds is None:
                    first_chunk_seconds = time.perf_counter() - started
                if args.stream_dir:
                    # Each chunk is independently decodable on iOS and desktop.
                    # File names are generated locally, never from caller input.
                    streamed = np.concatenate(
                        (wav, np.zeros(int(model.sr * pause), dtype=np.float32))
                    )
                    path = pathlib.Path(args.stream_dir) / (
                        "voice_" + str(uuid.uuid4()) + ".wav"
                    )
                    sf.write(path, streamed, model.sr, subtype="PCM_16")
                    os.chmod(path, 0o600)
                    print(
                        json.dumps(
                            {
                                "type": "audio",
                                "url": "/audio/" + path.name,
                                "duration": round(len(streamed) / model.sr, 3),
                            }
                        ),
                        flush=True,
                    )
                if index:
                    out.write(np.zeros(int(model.sr * pause), dtype=np.float32))
                out.write(wav)
                samples += len(wav)
    os.chmod(args.output, 0o600)
    print(
        json.dumps(
            {
                "engine": (
                    "chatterbox-multilingual" if multilingual else "chatterbox-nano"
                ),
                "method": (
                    "lora-fine-tuning"
                    if cfg.get("adapter")
                    else "reference-conditioning"
                ),
                "revision": cfg.get("revision", "reference-v1"),
                "deliveryMode": delivery.get("mode", "professional"),
                "seconds": round(samples / model.sr, 2),
                "generationSeconds": round(time.perf_counter() - started, 2),
                "peakMiB": round(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                ),
                "chunks": len(chunks),
                "modelReused": reused,
                "modelReadySeconds": round(model_ready - started, 4),
                "firstChunkSeconds": round(first_chunk_seconds, 4),
                "synthesisSeconds": round(time.perf_counter() - model_ready, 4),
                "retries": retries,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
