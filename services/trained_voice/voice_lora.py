"""Small GPT-2 projection adapters, merged at inference without extra layers."""

import math
import torch
from torch import nn
from safetensors.torch import load_file, save_file


class ProjectionAdapter(nn.Module):
    def __init__(self, base, rank=8, alpha=16):
        super().__init__()
        self.base = base
        self.scale = alpha / rank
        self.a = nn.Parameter(torch.empty(base.weight.shape[0], rank))
        self.b = nn.Parameter(torch.zeros(rank, base.weight.shape[1]))
        nn.init.kaiming_uniform_(self.a, a=math.sqrt(5))

    def forward(self, x):
        return self.base(x) + (x @ self.a @ self.b) * self.scale


def attach(t3, rank=8, alpha=16):
    for parameter in t3.parameters():
        parameter.requires_grad_(False)
    adapters = {}
    for name, module in list(t3.tfmr.named_modules()):
        if name.rsplit(".", 1)[-1] in {"c_attn", "c_proj", "c_fc"}:
            parent_name, child = name.rsplit(".", 1)
            parent = t3.tfmr.get_submodule(parent_name)
            adapter = ProjectionAdapter(module, rank, alpha)
            setattr(parent, child, adapter)
            adapters[name] = adapter
    if len(adapters) != len(t3.tfmr.h) * 4:
        raise ValueError("Unexpected Nano projection architecture")
    return adapters


def save(adapters, path):
    state = {}
    for name, layer in adapters.items():
        state[name + ".a"] = layer.a.detach().cpu().contiguous()
        state[name + ".b"] = layer.b.detach().cpu().contiguous()
    save_file(
        state,
        str(path),
        metadata={
            "format": "curie-nano-lora-v1",
            "rank": str(next(iter(adapters.values())).a.shape[1]),
            "scale": str(next(iter(adapters.values())).scale),
        },
    )


def merge(t3, path, strength=1.0):
    from safetensors import safe_open

    with safe_open(str(path), framework="pt") as f:
        metadata = f.metadata()
    if metadata.get("format") != "curie-nano-lora-v1":
        raise ValueError("Unrecognized local adapter format")
    scale = float(metadata["scale"]) * float(strength)
    if not math.isfinite(scale) or not 0 <= strength <= 1.5:
        raise ValueError("Invalid adapter strength")
    state = load_file(str(path), device="cpu")
    expected = {
        name
        for name, module in t3.tfmr.named_modules()
        if name.rsplit(".", 1)[-1] in {"c_attn", "c_proj", "c_fc"}
    }
    if set(state) != {name + suffix for name in expected for suffix in (".a", ".b")}:
        raise ValueError("Adapter target mismatch")
    deltas = []
    for name in sorted(expected):
        module = t3.tfmr.get_submodule(name)
        delta = (state[name + ".a"] @ state[name + ".b"]) * scale
        if delta.shape != module.weight.shape or not torch.isfinite(delta).all():
            raise ValueError("Invalid adapter weights")
        deltas.append((module, delta))
    with torch.no_grad():
        for module, delta in deltas:
            module.weight.add_(delta.to(module.weight.device, module.weight.dtype))
