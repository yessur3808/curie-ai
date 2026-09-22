# Phase 11 rollout stabilization migration

Phase 11 makes the typed turn pipeline authoritative, adds runtime-controlled
canaries and circuit breaking, and expands the release contract to 32 blocking
gates. It does not change SQLite or PostgreSQL schemas and does not require a
production credential for verification.

## Before rollout

1. Keep Curie stopped while updating the working tree.
2. Run `python -m evaluation.release` and require all 32 gates to pass.
3. Scan the generated report with
   `python scripts/scan_runtime_artifacts.py evaluation/reports/latest.json`.
4. Inspect `python -m scripts.pipeline_rollout status`. Remove an obsolete
   local override only after recording why it is safe.
5. Confirm the temporary legacy owner and 2026-10-22 removal deadline.

## Controlled rollout

The checked-in default is `default_active`; existing deployments can still
walk the seven stages in the operations runbook before resuming the service.
Start Curie only on explicit operator instruction. For device canaries, use an
exact reversible device target and verify readback. Expand one connector at a
time after its conformance tests and delivery receipts pass.

## Rollback

Trip the circuit breaker first:

```bash
python -m scripts.pipeline_rollout rollback wrong_device_mutation
```

Subsequent turns use legacy immediately. If application rollback is necessary,
keep Curie stopped and restore
`bb258494`. No database rollback is required.
Do not delete the small rollout-state file until its redacted incident metadata
has been reviewed.
