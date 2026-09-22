# Phase 11 rollout and rollback runbook

Curie's typed pipeline is the default. The prior whole-turn path remains only
as a temporary circuit-breaker owned by `curie-maintainers` until 2026-10-22.
Runtime state is credential-free, mode `0600`, and defaults to
`~/.curie/pipeline-rollout.json`.

## Inspect and control

```bash
python -m scripts.pipeline_rollout status
python -m scripts.pipeline_rollout set-stage shadow
python -m scripts.pipeline_rollout rollback wrong_device_mutation
python -m scripts.pipeline_rollout clear-rollback
```

Stage changes and rollback affect subsequent turns without restarting Curie.
The valid progression is `offline`, `shadow`, `conversation_canary`,
`smart_home_canary`, `connector_expansion`, `default_active`, and finally
`legacy_removed`.

Conversation and smart-home canaries require an owner in
`CURIE_TURN_PIPELINE_ACTIVE_OWNERS`. Smart-home mutation canaries additionally
require an exact normalized target in `CURIE_TURN_PIPELINE_DEVICE_CANARY` and
mark verification as mandatory. Connector expansion activates only connectors
listed in `CURIE_TURN_PIPELINE_ACTIVE_CONNECTORS`; other connectors remain in
side-effect-free shadow mode.

## Automatic rollback triggers

The circuit breaker covers wrong-device mutation, false verification claims,
cross-owner data, excessive tool activation, increased delivery failures, two
consecutive p95 latency regressions, unsupported proactive claims, increased
instability, and secret exposure. A corrupt or unsupported rollout-state file
also fails closed to legacy.

Do not clear a rollback until its incident evidence is understood and the full
32-gate release command passes. The `/health` and `/readiness` payload includes
the effective stage, circuit-breaker state, and legacy-retention metadata.

## Legacy removal criteria

Move to `legacy_removed` only after the observation window contains no
unexplained shadow disagreement, every gate passes, operational rollback has
been drilled, all callers use the typed contract, and runbooks and obsolete
tests are updated. Production SLO observations must replace the scorecard rows
explicitly labeled `offline_proxy`; offline success is not a claim of measured
production availability.
