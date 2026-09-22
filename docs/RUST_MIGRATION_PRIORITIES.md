# Rust migration priorities

## Decision summary

Do not rewrite Curie wholesale. The highest-value plan is to keep Python as the
fast-changing orchestration and model-integration layer, then replace measured
CPU-, memory-, and concurrency-heavy boundaries with small Rust components.
This preserves Curie's behavior while earning resource savings where Rust can
actually provide them.

The recommended order is:

1. memory retrieval and ranking kernel;
2. audio/media transport and validation worker;
3. connector ingress, queueing, deduplication, and delivery gateway;
4. deterministic entity/device resolution kernel;
5. durable task and idempotency engine.

Each migration is gated by an identical-input parity corpus, resource
benchmarks, failure injection, and a Python rollback path. Estimates below are
engineering estimates, not promises; profiling on the production server is the
entry gate for every item.

## Scoring method

Priority combines four factors on a five-point scale:

- **resource benefit**: expected CPU, resident-memory, allocation, or process
  savings in the measured hot path;
- **reliability benefit**: stronger bounds, backpressure, cancellation, and
  concurrency safety;
- **boundary clarity**: how easily the component can accept typed input and
  return typed output without importing Curie's changing policy;
- **migration risk**: semantic drift, operational complexity, and rollback
  difficulty. Lower is better.

| Priority | Candidate | Resource | Reliability | Boundary | Risk | Initial effort |
|---:|---|---:|---:|---:|---:|---:|
| 1 | Memory retrieval/ranking kernel | 4 | 3 | 5 | 2 | 2–4 weeks |
| 2 | Audio/media transport worker | 4 | 5 | 4 | 3 | 3–5 weeks |
| 3 | Connector delivery gateway | 4 | 5 | 4 | 4 | 6–10 weeks |
| 4 | Entity/device resolver kernel | 2 | 4 | 5 | 2 | 3–5 weeks |
| 5 | Durable task/idempotency engine | 2 | 5 | 4 | 4 | 6–8 weeks |

The estimates assume one experienced engineer, existing tests remain blocking,
and the Python implementation stays available during canary rollout.

## Priority 1: memory retrieval and ranking kernel

### Scope

Move only the deterministic retrieval hot path into a PyO3 extension:

- candidate filtering and owner/namespace enforcement;
- temporal decay and salience scoring;
- token-budget packing;
- deduplication and diversity selection;
- compact serialization of ranking evidence.

Keep memory policy, learning approval, database migrations, summarization,
embeddings, and prompt construction in Python. Rust must never decide what is
safe to remember or expand an owner's authority.

### Why first

This boundary is typed, side-effect-light, called on many conversational turns,
and easy to compare against the current implementation. Rust can remove Python
object churn and speed scoring over large candidate sets without forcing a
service split. A native extension also avoids network and serialization costs.

### Acceptance gates

- Bit-for-bit owner and namespace isolation on the privacy corpus.
- Identical top-k ordering for fixed timestamps and scores, or a documented
  versioned ranking change that passes all memory evaluations.
- At least 2x throughput or 35% lower CPU time on 1k, 10k, and 100k candidate
  benchmarks; otherwise retain Python.
- Peak allocation and p95 latency recorded before and after.
- Feature-flagged Python fallback and no database format change.

## Priority 2: audio and media transport worker

**Implemented in September 2026.** The bounded PyO3 worker now covers streaming
inspection and hashing, signature validation, PCM/WAV concatenation, and
cancellable subprocess supervision. See
[Rust audio and media transport](RUST_MEDIA_TRANSPORT.md) for the live boundary,
verification, and rollback contract.

### Scope

Build a small Rust process or library for bounded byte-oriented work:

- streaming reads/writes and size limits;
- MIME sniffing, file-signature checks, hashing, and safe temporary files;
- WAV framing, PCM concatenation, and chunk manifests;
- subprocess lifecycle, cancellation, deadlines, and output validation;
- optional Opus/FFmpeg supervision where a mature library or the existing
  FFmpeg binary remains the codec implementation.

Keep Chatterbox, Torch, Whisper, generation, voice-persona decisions, and model
weights in Python. Rewrapping Torch in Rust would not reduce the trained voice's
roughly 4.4–5.6 GiB model-worker peak and would add a fragile FFI boundary.

### Why second

Media paths allocate large buffers, cross process boundaries, and need strict
limits. Rust improves streaming memory behavior and makes cancellation and
cleanup easier to prove. It also complements the current one-worker lease: Rust
can supervise the Python inference child without becoming the inference engine.

### Acceptance gates

- Exact parity for supported formats and rejection reasons.
- Constant-memory streaming for configured maximum files.
- No orphaned workers or temporary files in cancellation/timeout drills.
- At least 30% lower non-model peak RSS for representative attachment and
  speech-stream workloads.
- Malformed corpus passes under sanitizers and fuzzing.

## Priority 3: connector delivery gateway

### Scope

After profiling proves connector overhead is material, move the shared
transport mechanics—not channel personality—into a Rust sidecar:

- inbound webhook/socket envelopes and schema validation;
- bounded queues and per-owner/channel backpressure;
- message deduplication and idempotency receipts;
- rate limiting, retry schedules, and delivery receipts;
- attachment streaming and graceful shutdown.

Telegram-, Discord-, Slack-, and WhatsApp-specific presentation can remain in
Python initially. The gateway communicates through a versioned local protocol;
it never constructs Curie's prose or chooses tools.

### Why third

A long-lived Rust gateway can reduce baseline memory compared with several
Python connector runtimes and make overload behavior more predictable. The
operational surface is larger, however, so it should follow two smaller
migrations and reuse their telemetry and rollout method.

### Acceptance gates

- No dropped or duplicated side effects in restart, timeout, and redelivery
  drills.
- Existing connector and Telegram formatting corpora remain green.
- p99 acknowledgement latency improves under burst load.
- Measured baseline RSS reduction justifies the extra service.
- Shadow traffic, owner canary, per-connector rollout, and instant rollback.

## Priority 4: deterministic entity and device resolver

### Scope

Port canonicalization, Unicode normalization, alias lookup, weighted fuzzy
matching, group expansion, and confidence calculation to a small PyO3 library.
Return ranked candidates and evidence. Keep clarification policy, permissions,
mutation planning, execution, and verification in Python.

### Why fourth

The boundary is clean and Rust can make matching predictable and fast across a
large inventory. Curie's current household inventory is small, so absolute
resource savings will probably be modest. This is more valuable for rigor and
fuzz safety than for server cost and should not displace the first three items.

### Acceptance gates

- Exact pass on every existing device-resolution and ambiguity test.
- Property tests for spelling variants, punctuation, Unicode, groups, and
  adversarial near-matches.
- Never auto-select an ambiguous mutation target.
- Versioned scores and explanation evidence remain visible to Curie's planner.

## Priority 5: durable task and idempotency engine

### Scope

Consider a Rust service for persistent schedules, dependency transitions,
leases, retries, cancellation, idempotency receipts, and crash recovery. Keep
natural-language planning and capability selection in Python.

### Why fifth

This can substantially improve reliability for reminders, proactive work, and
multi-step actions, but its main gain is correctness rather than raw efficiency.
It also touches durable state and therefore has the highest migration and
rollback burden. Begin only after the task contract is stable and production
traces show the Python scheduler is a bottleneck or reliability risk.

## Components that should remain in Python

- **LLM and multimodal orchestration:** providers, prompt policy, tool routing,
  personality, and evaluation rules change rapidly and spend most time outside
  the Python interpreter.
- **Chatterbox/Torch and Whisper model execution:** the heavy kernels are
  already native; Rust glue does not shrink model weights or inference RAM.
- **llama.cpp inference:** it is already C/C++ and reached through a native
  binding or process boundary.
- **Learning and memory policy:** consent, provenance, promotion, rollback, and
  self-improvement gates benefit from readability and rapid auditing.
- **One-off integrations:** low-volume provider adapters will not repay a
  second-language implementation until shared transport is extracted.

## Required measurement before coding

Capture a 24-hour representative baseline without message content or secrets:

- process RSS/PSS, CPU seconds, allocation rate, file descriptors, and thread
  count by service;
- p50/p95/p99 stage latency from existing turn traces;
- memory candidate counts and ranking time;
- connector queue depth, retries, acknowledgement, and delivery time;
- media bytes copied, temporary storage, worker lifetime, and cancellation;
- trained-voice inference RSS separately from transport RSS.

Use `py-spy` or sampling profiles for Python CPU, `memray` or equivalent for
allocation tests, `/usr/bin/time -v` for isolated workers, and the existing
privacy-safe observability fields for stage timing. Do not profile raw message
content in production.

## Recommended delivery sequence

1. Record the baseline and set numerical success thresholds.
2. Freeze versioned input/output schemas and build replay corpora.
3. Ship the memory kernel behind an environment flag; run offline parity,
   shadow comparison, then an owner canary.
4. Re-measure. Keep the Rust component only if it clears its resource gate.
5. Build the media supervisor and fuzz its parsers and cancellation paths.
6. Re-measure connector baseline after those savings.
7. Build the gateway only if projected memory/latency savings still justify its
   operational cost.
8. Port device matching when inventory scale or resolver profiles warrant it.
9. Treat the durable task engine as a separate reliability project with a data
   migration, dual-write validation, and rehearsed rollback.

This sequence targets real resource use while avoiding a multi-month rewrite
that would pause Curie's reasoning, personality, and capability improvements.
