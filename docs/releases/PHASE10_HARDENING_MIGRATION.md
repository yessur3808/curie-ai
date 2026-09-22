# Phase 10 operational hardening migration

Phase 10 adds observability, service objectives, bounded execution, recursive
secret redaction, artifact scanning, and offline recovery drills. There is no
database schema migration and no production credential is required to verify
the release.

## Before rollout

1. Keep Curie stopped while updating the working tree.
2. Run `python -m evaluation.release` and require all 24 gates to pass.
3. Run `python scripts/scan_runtime_artifacts.py` against newly generated
   release reports and private trace files. Do not upload trace files.
4. Review optional capacity overrides. Defaults are conservative:
   `GLOBAL_READ_CONCURRENCY_LIMIT=16` and
   `OWNER_MUTATION_CONCURRENCY_LIMIT=1`.
5. Review any `SLO_<STAGE>_P95_MS` override against actual hardware. An
   override changes the dashboard target, not the stage timeout.

## Rollout

Deploy the application commit and start Curie only when an operator chooses to
resume service. Confirm `/health` reports the independent capability list,
capacity state, and no unexpected SLO breach after representative requests.
Trace files are private, mode 0600, bounded, and rotated once.

## Rollback

Stop Curie, restore application commit `ecd7996a`, and restart only after the
operator confirms the previous configuration. No SQLite or PostgreSQL rollback
is needed. The new trace and telemetry files can be retained because older code
ignores their added fields; delete them only under the normal private log
retention policy.
