# Phase 3–5 migration and rollback

This release is application-compatible with existing connector and provider
configuration. No credential changes are required.

## Before rollout

1. Back up Curie's SQLite or configured database through the existing backup
   command.
2. Run the full offline test suite and `python -m evaluation.phase345_suite`.
3. Keep Curie stopped until the release is reviewed. Starting the daemon is a
   separate operational decision.

## Data changes

The local SQLite store creates an additive `mutation_attempts` table on first
use. It stores owner-scoped hashes, idempotency state, sanitized receipts, and
timestamps. Existing conversation, memory, device, and task records are not
rewritten. External repository backends use the equivalent additive
collection.

Rejected device aliases are stored as owner-scoped tombstones. This is
intentional: after a correction such as “There is no device called
DreamView,” that wording cannot silently resolve to another device.

## Configuration

All new settings have safe defaults:

- `INTENT_CLASSIFIER_ABSTAIN_THRESHOLD=0.78`
- `HOME_INVENTORY_REFRESH_SECONDS=30`
- `HOME_INVENTORY_STALE_SECONDS=300`
- `DEVICE_FUZZY_MUTATION_THRESHOLD=0.90`
- `DEVICE_FUZZY_READ_THRESHOLD=0.82`
- `DEVICE_FUZZY_AMBIGUITY_GAP=0.10`
- `CURIE_APPROVAL_TTL_MINUTES=30`

## Rollback

Stop Curie, restore the prior reviewed revision, and restart it only when
requested. The additive mutation table can remain because older releases do
not read it. Restore the pre-rollout database backup only if application data
also needs to be reverted; restoring it will discard post-backup memory and
conversation records.
