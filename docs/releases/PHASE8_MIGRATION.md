# Phase 8 controlled-learning migration and rollback

This release adds a guarded adaptation subsystem. It does not start Curie,
change credentials, enable a connector, or activate an inferred candidate.

## Before rollout

1. Keep Curie stopped and back up the configured memory database.
2. Run the full offline suite and `python -m evaluation.phase8_suite`.
3. Review any existing implicit adaptation preferences. Existing values remain
   valid Level 1 settings; only future implicit signals use the candidate path.
4. Start Curie separately after the release and configuration are approved.

## Data changes

SQLite migration 5 adds three owner-scoped tables:

- `learning_events` for redacted, immutable outcome provenance;
- `learning_candidates` for deduplicated proposals and gate evidence; and
- `adaptive_config_versions` for shadow, canary, active, superseded, and rolled
  back configurations.

Existing conversation, profile, memory, task, and action records are not
rewritten. MongoDB deployments create equivalent collections on first use.
No PostgreSQL schema change is required.

## Safe defaults

- `LEARNING_SESSION_TTL_MINUTES=180`
- `LEARNING_LEVEL2_MIN_EVIDENCE=3`
- `LEARNING_SHADOW_MIN_OBSERVATIONS=20`
- `LEARNING_CANARY_MIN_OBSERVATIONS=20`

No inferred configuration is active by default. Level 2 needs explicit owner
approval after evaluation and shadow review. Level 3 additionally needs an
opted-in canary. Level 4 cannot merge or deploy through this subsystem.

## Rollback

Use `/learning rollback <config_key>` for one adaptive configuration. Use
`/reset_preferences <setting>` to remove a promoted presentation preference
without restoring an older adaptive value.

For an application rollback, stop Curie and restore commit `6ba0c1c9` or the
previous reviewed release. The additive Phase 8 tables may remain because the
older release does not read them. To remove only the local Phase 8 schema, run
the SQLite migration rollback to target 4 after taking a backup. Removing those
tables permanently discards learning events, candidates, and adaptive config
history; it does not delete ordinary conversation memory.
