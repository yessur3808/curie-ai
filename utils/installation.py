"""Bind provisioned installations before any application modules load."""

import json
import os
from pathlib import Path


def bootstrap(root):
    root = Path(root).resolve()
    marker = root / "state/installation.json"
    if not marker.exists():
        return  # Legacy installations retain their existing configuration.
    manifest = json.loads(marker.read_text())
    values = {}
    for line in (root / ".env").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        try:
            value = json.loads(value)
        except ValueError:
            value = value.strip("'\"")
        values[key.strip()] = str(value)
    if (
        values.get("CURIE_INSTANCE") != manifest["name"]
        or values.get("PERSONA_FILE") != manifest["persona"]
    ):
        raise RuntimeError(
            "Personality is bound to this installation; create a new installation to change it."
        )
    for key, relative in {
        "CURIE_ROOT": ".",
        "ASSISTANT_STATE_DIR": "state",
        "CURIE_LOCAL_MEMORY_DB": "state/memory.sqlite3",
        "CURIE_INFERENCE_SOCKET": "log/inference/worker.sock",
    }.items():
        if Path(values.get(key, "")).resolve() != root / relative:
            raise RuntimeError(f"{key} must belong to this installation.")
    # An exported token/database URL from another terminal must not bleed in.
    retained = {
        k: v
        for k, v in os.environ.items()
        if k
        in {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "TZ",
            "TMPDIR",
            "TERM",
            "SYSTEMROOT",
            "CUDA_VISIBLE_DEVICES",
            "LD_LIBRARY_PATH",
        }
    }
    os.environ.clear()
    os.environ.update(retained)
    os.environ.update(values)
