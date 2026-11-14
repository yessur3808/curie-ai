#!/usr/bin/env python3
"""Read-only, fail-closed gate for promoting this host's verified GPU test boot."""

import json
from pathlib import Path
import stat
import time

KERNEL = "7.0.0-43-generic"
EVIDENCE = Path("/var/lib/curie-recovery/gpu-validation.json")


def validate(evidence, *, boot_id, kernel, cmdline, age_seconds):
    if kernel != KERNEL or "curie_gpu_test=1" not in cmdline.split():
        raise ValueError("Not running the expected one-time GPU test kernel")
    if not 0 <= age_seconds <= 180:
        raise ValueError("GPU verification is stale; wait for a fresh check")
    if not isinstance(evidence, dict):
        raise ValueError("Invalid GPU verification record")
    if evidence.get("boot_id") != boot_id or evidence.get("kernel") != kernel:
        raise ValueError("GPU verification belongs to another boot or kernel")
    required = ("render", "amdgpu_driver", "hardware_vulkan", "remote_ui", "passed")
    if not all(evidence.get(key) is True for key in required):
        raise ValueError("GPU hardware and remote-access checks must all pass")


def check():
    info = EVIDENCE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("GPU verification must be a root-owned, protected file")
    validate(
        json.loads(EVIDENCE.read_text()),
        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        kernel=Path("/proc/sys/kernel/osrelease").read_text().strip(),
        cmdline=Path("/proc/cmdline").read_text(),
        age_seconds=time.time() - info.st_mtime,
    )


if __name__ == "__main__":
    try:
        check()
    except (OSError, ValueError) as error:
        raise SystemExit("GPU promotion blocked: " + str(error))
    print("Fresh, same-boot AMD GPU and remote-access verification passed.")
