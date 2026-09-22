"""Credential-free synthetic benchmark for device/entity resolution."""

from __future__ import annotations

import argparse
import json
import os
import time

from agent.understanding.entities import DeviceResolver, device_resolver_status
from services.smart_home.aliases import normalize_alias
from services.smart_home.models import CanonicalDevice, DeviceSnapshot


def _devices(count: int) -> list[CanonicalDevice]:
    values = []
    for index in range(count):
        kind = "light" if index % 3 else "switch"
        name = f"Room {index % 20} {'Lamp' if kind == 'light' else 'Switch'} {index}"
        snapshot = DeviceSnapshot(
            "synthetic",
            str(index),
            name,
            kind,
            True,
            "off",
            False,
            True,
            {},
            {"room": f"room {index % 20}"},
        )
        values.append(
            CanonicalDevice.from_snapshot(
                snapshot, normalized_name=normalize_alias(name)
            )
        )
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--devices", type=int, default=1_000)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--mode", choices=("auto", "rust", "python"), default="auto")
    args = parser.parse_args()
    count = max(1, min(50_000, args.devices))
    iterations = max(1, min(10_000, args.iterations))
    os.environ["CURIE_DEVICE_RESOLVER"] = args.mode
    devices = _devices(count)
    resolver = DeviceResolver()
    label = "Switch" if (count - 1) % 3 == 0 else "Lamp"
    target = f"Room {(count - 1) % 20} {label} {count - 1}"
    started = time.perf_counter()
    resolved = 0
    for _ in range(iterations):
        result = resolver.resolve(target, devices)
        resolved += int(result.resolved)
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "backend": device_resolver_status()["active"],
                "devices": count,
                "iterations": iterations,
                "resolved": resolved,
                "milliseconds": round(elapsed * 1000, 3),
                "queries_per_second": round(iterations / max(elapsed, 1e-9), 1),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
