# Unified Rust runtime kernel

Curie's runtime kernel is one coarse-grained ABI3 extension, not a collection
of per-function foreign calls. Rust owns bounded deterministic mechanisms;
Python keeps conversation policy, personality, provider SDKs, permissions,
model inference, and response wording.

## Native ownership

- A process-resident SQLite connection with explicit transactions, WAL, busy
  timeout, foreign keys, compatibility migrations, and a generic DB-API bridge.
- An append-only event store with stream ordering and optional durable dedupe.
- Connector ingress admission, expiry, duplicate suppression, and completed
  response replay before memory, tool, or model work.
- NFC normalization, punctuation and whitespace normalization, tokenization,
  and deterministic action, state, group, negation, URL, and email features.
- Recursive credential redaction, a bounded telemetry buffer, file rotation,
  and native JSONL serialization.
- Bounded text, DOCX, and ODT processing with archive traversal, member-count,
  compressed-source, expanded-byte, and extracted-character limits. PDF worker
  bytes use the separately audited Rust media transport.
- A model-residency registry with load admission, LRU eviction candidates,
  active leases, byte accounting, and content-free health snapshots.

The Rust task engine also owns persistent scheduled work. Cron, reminders, and
proactive checks use atomic claims, expiring leases, fencing tokens, retries,
and recurrence calculation. Python only performs the claimed conversation or
delivery work and reports its result.

## Modes and model-supervisor gate

`CURIE_RUNTIME_KERNEL` accepts `rust`, `auto`, or `python`. Production should
use `rust`, which fails closed if the wheel is unavailable. `python` is the
audited emergency rollback and does not require a data conversion.

`CURIE_MODEL_SUPERVISOR` defaults to `profiled`. The Rust supervisor is built
and tested but does not alter model loading until a live profile demonstrates
duplicate residency or worker contention. Set it to `rust` only after that
evidence is captured. This avoids adding lifecycle control merely because it
exists.

## Build and verify

```bash
make runtime-kernel
make task-engine
make runtime-kernel-check
```

The strict check covers formatting, Clippy warnings, Rust unit tests, Python
integration, concurrent ingress admission, persistent connections, event
dedupe, scheduled-work leases, stale fencing, redaction, JSONL telemetry,
archive traversal, extraction bounds, and model residency.

Run a content-safe comparison without starting Curie:

```bash
CURIE_TASK_ENGINE=rust python -m scripts.profile_runtime_kernel \
  --mode both --iterations 500
```

The profile reports only timings, process high-water RSS, thread count, model
basenames and file sizes, configured residency, and worker counts. It does not
emit prompts, messages, credentials, or extracted document content.

### Recorded isolated-server baseline

On 2026-09-23, with 500 persistence iterations and 5,000 scheduler/language
iterations, the candidate produced:

| Workload | Python | Rust | Result |
| --- | ---: | ---: | ---: |
| SQLite persistence | 27.530 ms | 3.506 ms | 7.9x throughput |
| Schedule matching | 12.608 ms | 4.342 ms | 2.9x faster |
| Language preprocessing | 13.495 ms | 21.218 ms | 4.2 microseconds/call in Rust |

The language comparison is intentionally conservative: Rust returns normalized
text plus deterministic tokens, action/state/quantifier/negation signals, and
URL/email features, while the Python side only normalizes text. Process
high-water RSS was 107,454,464 bytes after the Python pass and 110,899,200
bytes after the subsequent Rust pass. Because high-water RSS is cumulative in
one process, that figure is a safety observation, not an allocator benchmark.

No model was resident and the profiled worker count was one. The run therefore
found no evidence of duplicate model residency or worker contention. The model
supervisor remains `profiled` rather than production-active.

## Rollback

1. Set `CURIE_RUNTIME_KERNEL=python`.
2. Leave `CURIE_TASK_ENGINE=rust` unless task scheduling itself is implicated;
   set it to `python` only for the separate task-engine rollback.
3. Restart Curie and verify `/health`.

SQLite tables are additive and compatible. Rollback requires no deletion or
conversion. In-flight native ingress and scheduling leases expire naturally.
