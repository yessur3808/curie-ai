# Rust durable task and idempotency engine

Curie's durable execution state machine is an ABI3 PyO3 extension backed by
the existing local SQLite task store. It replaces the coordination-sensitive
Python hot path while keeping capability policy and Curie's behavior in the
auditable orchestration layer.

## Native responsibilities

The Rust engine owns:

- deterministic dependency-graph validation and topological checks;
- canonical SHA-256 graph and step idempotency keys;
- atomic owner-scoped create-or-replay transactions;
- detection of an idempotency key reused for a different graph;
- dependency-aware step claims with expiring leases;
- monotonically increasing fencing tokens that reject stale workers;
- lease heartbeats for long-running capabilities;
- persisted attempt counts and bounded exponential retry timestamps;
- cancellation, deadline expiry, terminal reconciliation, and finalization;
- atomic per-step result/evidence patches, so parallel workers cannot overwrite
  one another's completed state.

The engine preserves the existing `durable_tasks.document_json` format. A
small `durable_task_leases` table stores only task IDs, step IDs, opaque worker
IDs, lease timestamps, tokens, and active state. No prompt, credential, chat
message, or provider payload is added to the lease table.

## Python responsibilities

Python continues to own:

- natural-language planning and capability selection;
- registry/schema validation and permission checks;
- approval creation and owner-scoped approval consumption;
- actual tool and provider execution;
- concurrency budgets for read-only capabilities;
- output criteria, evidence, and post-mutation verification;
- compensation policy, audit normalization, progress messages, and Curie's
  personality and response wording.

An interrupted read may be claimed again only within its configured attempt
limit. An interrupted mutation is never replayed: the new worker receives a
reconciliation claim and runs the required read-only verification steps. A
stale worker cannot publish a result after another worker acquires a newer
fencing token.

## Modes, build, and rollback

`CURIE_TASK_ENGINE` accepts:

- `rust`: require the native engine and fail readiness closed if unavailable;
- `auto`: use Rust when installed, otherwise use the audited Python path;
- `python`: force the rollback implementation.

Production should use `rust`. Build and verify with:

```bash
make task-engine
make task-engine-check
```

`CURIE_TASK_LEASE_MS` controls the claim lease and is bounded to 1–300 seconds.
`CURIE_TASK_RETRY_BASE_MS` controls the persisted exponential retry base and is
bounded to 0–30 seconds. Retry timestamps are capped at five minutes.

Rollback does not require a data conversion: set `CURIE_TASK_ENGINE=python`
and restart the service. Python can still read every task document. The lease
table may remain in place and is removed only by rolling SQLite migration 6
back to migration 5.

## Verification contract

The blocking native CI lane requires:

- `cargo fmt`, Clippy with warnings denied, Rust unit tests, and Cargo audit;
- ABI3 wheel build and installation;
- concurrent atomic create-or-replay with one retained task;
- two-worker exactly-once claim behavior;
- expired-lease recovery and stale-token rejection;
- owner isolation and strict-mode fail-closed readiness;
- existing task runtime, planning, retry, and migration suites;
- a bounded graph validation and hashing benchmark smoke test.

This migration primarily improves correctness and recovery. Benchmark results
are recorded for regression detection, not used to claim that SQLite-backed
durability is a CPU optimization.
