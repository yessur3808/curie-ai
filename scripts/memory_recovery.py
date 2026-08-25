#!/usr/bin/env python3
"""Operator CLI for verified Curie SQLite backups and explicit restoration."""

from __future__ import annotations

import argparse
import json

from memory.backup import create_backup, restore_backup, verify_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("destination")
    verify = commands.add_parser("verify")
    verify.add_argument("source")
    verify.add_argument("--sha256")
    restore = commands.add_parser("restore")
    restore.add_argument("source")
    restore.add_argument("--sha256")
    restore.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.command == "backup":
        result = create_backup(args.destination)
    elif args.command == "verify":
        result = verify_backup(args.source, args.sha256)
    else:
        if not args.confirm:
            parser.error("restore requires --confirm after the backup is verified")
        result = restore_backup(args.source, expected_sha256=args.sha256)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
