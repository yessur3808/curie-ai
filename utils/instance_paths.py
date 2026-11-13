"""Application state; retain legacy Curie paths until explicitly migrated."""

import os
from pathlib import Path


def assistant_state_dir():
    configured = os.environ.get("ASSISTANT_STATE_DIR")
    if configured:
        return Path(configured)
    root = Path(__file__).resolve().parents[1]
    if (root / "state/installation.json").exists():
        return root / "state"
    return Path.home() / ".curie"
