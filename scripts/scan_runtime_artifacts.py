#!/usr/bin/env python3
"""Fail when Curie logs or evaluation artifacts contain credential shapes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.redaction import secret_markers


_SUFFIXES = {".json", ".jsonl", ".log", ".txt"}


def _files(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    found: list[Path] = []
    for value in paths:
        path = Path(value).expanduser()
        if path.is_dir():
            found.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.casefold() in _SUFFIXES
            )
        elif path.is_file():
            found.append(path)
    return tuple(sorted(set(found)))


def scan_paths(paths: Iterable[str | Path]) -> dict:
    findings: list[dict[str, object]] = []
    files = _files(paths)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                {
                    "path": str(path),
                    "markers": ["unreadable"],
                    "error_type": type(exc).__name__,
                }
            )
            continue
        markers = set(secret_markers(text))
        try:
            if path.suffix.casefold() == ".json":
                markers.update(secret_markers(json.loads(text)))
            elif path.suffix.casefold() == ".jsonl":
                for line in text.splitlines():
                    if line.strip():
                        markers.update(secret_markers(json.loads(line)))
        except (TypeError, ValueError):
            markers.add("invalid_structured_artifact")
        if markers:
            findings.append({"path": str(path), "markers": sorted(markers)})
    return {
        "schema_version": 1,
        "files_scanned": len(files),
        "passed": not findings,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    report = scan_paths(args.paths)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
