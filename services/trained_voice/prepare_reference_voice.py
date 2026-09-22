#!/usr/bin/env python3
"""Create a reusable speaker conditioning profile from a local reference clip.
This does not train or fine-tune the base model's weights.
"""

import argparse
import hashlib
import json
import os
import pathlib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    os.environ.update(
        OMP_NUM_THREADS="6",
        MKL_NUM_THREADS="6",
        TOKENIZERS_PARALLELISM="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    model = ChatterboxTurboTTS.from_local(args.model, device="cpu", nano=True)
    # Preserve float32 with NumPy 2; upstream LUFS normalization promotes to float64.
    model.prepare_conditionals(args.reference, norm_loudness=False)
    target = pathlib.Path(args.output)
    model.conds.save(target)
    os.chmod(target, 0o600)
    target.with_suffix(".json").write_text(
        json.dumps(
            {
                "method": "reference-conditioning",
                "engine": "chatterbox-nano",
                "referenceSha256": hashlib.sha256(
                    pathlib.Path(args.reference).read_bytes()
                ).hexdigest(),
                "weightFineTuning": False,
            },
            indent=2,
        )
    )
    print("Speaker profile created; base model weights are unchanged.")


if __name__ == "__main__":
    main()
