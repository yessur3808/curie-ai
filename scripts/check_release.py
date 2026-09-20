"""Fail CI when a significant release omits required evidence or rollback."""

from __future__ import annotations

import json
from pathlib import Path

from contracts import CONTRACT_VERSION, contract_catalog


def validate_release(root: str | Path = ".") -> dict:
    root = Path(root)
    manifest = json.loads((root / "release/current.json").read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "release",
        "contract_version",
        "changelog",
        "migration_notes",
        "evaluation_delta",
        "rollback",
    }
    missing = required - set(manifest)
    if missing:
        raise ValueError(f"Release manifest is missing: {', '.join(sorted(missing))}")
    if manifest["contract_version"] != CONTRACT_VERSION:
        raise ValueError("Release and runtime contract versions differ")
    if contract_catalog()["contract_version"] != CONTRACT_VERSION:
        raise ValueError("Contract catalog version is inconsistent")
    for field in ("changelog", "migration_notes", "evaluation_delta"):
        path = root / manifest[field]
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise ValueError(f"Release artifact is missing or empty: {path}")
    changelog = (root / manifest["changelog"]).read_text(encoding="utf-8")
    if manifest["release"] not in changelog:
        raise ValueError("Changelog does not describe the current release")
    delta = json.loads(
        (root / manifest["evaluation_delta"]).read_text(encoding="utf-8")
    )
    gate_config = json.loads(
        (root / "evaluation/release_gates.json").read_text(encoding="utf-8")
    )
    gates = gate_config.get("gates", gate_config)
    for name, threshold in delta.get("required_gates", {}).items():
        configured = gates.get(name)
        if isinstance(configured, dict):
            configured = configured.get("threshold")
        if configured is None or float(configured) < float(threshold):
            raise ValueError(f"Release gate {name} is below the required delta")
    rollback = manifest["rollback"]
    if not rollback.get("application") or "data_backup_required" not in rollback:
        raise ValueError("Release rollback instructions are incomplete")
    return manifest


if __name__ == "__main__":
    release = validate_release()
    print(f"Release evidence valid: {release['release']}")
