#!/usr/bin/env python3
"""Inspect or change Curie's local turn-pipeline rollout state."""

from __future__ import annotations

import argparse
import json

from agent.kernel.rollout import (
    RollbackTrigger,
    RolloutStage,
    clear_pipeline_rollback,
    rollout_status,
    set_rollout_stage,
    trigger_pipeline_rollback,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status")
    stage = subcommands.add_parser("set-stage")
    stage.add_argument("stage", choices=[item.value for item in RolloutStage])
    trip = subcommands.add_parser("rollback")
    trip.add_argument("trigger", choices=[item.value for item in RollbackTrigger])
    subcommands.add_parser("clear-rollback")
    args = parser.parse_args()

    if args.command == "set-stage":
        set_rollout_stage(args.stage)
    elif args.command == "rollback":
        trigger_pipeline_rollback(args.trigger, evidence={"source": "operator"})
    elif args.command == "clear-rollback":
        clear_pipeline_rollback()
    print(json.dumps(rollout_status(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
