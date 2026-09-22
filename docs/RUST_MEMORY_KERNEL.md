# Rust memory retrieval and ranking kernel

## Scope

Curie's native module owns the deterministic, CPU-heavy portion of long-term
memory recall:

- normalization, stemming, and semantic alias expansion;
- lexical, phrase, Python-compatible fuzzy, and hashed-vector matching;
- confidence, importance, reinforcement, and temporal-decay scoring;
- bounded candidate reranking and key/value deduplication;
- core, episodic, and archival tier limits;
- strict context-character budget packing;
- a bounded native LRU of compiled memory features.

Python continues to own durable storage, schema migrations, learning consent,
candidate promotion, prompt construction, and tool policy. `MemoryService`
filters records by owner before ranking; the Rust kernel independently checks
the expected owner again before considering a record. The extension receives a
minimal typed projection and returns selected source indices, relevance,
reason, and tier. It never receives credentials or opens the database.

## Build and activate

Rust is a build dependency, not a Python runtime dependency. From the Curie
checkout:

```sh
make memory-kernel
CURIE_MEMORY_KERNEL=rust .venv/bin/python -c \
  'from memory import memory_kernel_status; print(memory_kernel_status())'
```

The extension uses Python's stable ABI from Python 3.10 onward. Its compiled
shared object is a deployment artifact and remains excluded from Git. Source
and `Cargo.lock` are committed.

`CURIE_MEMORY_KERNEL` accepts:

- `auto` (default): use Rust when installed; otherwise use Python. A native
  runtime error is recorded and falls back once with a content-free warning.
- `rust`: require the native module and fail closed if it is absent or errors.
  Use this on a provisioned production host.
- `python`: force the auditable Python implementation for rollback and parity
  investigation.

Changing the setting requires restarting the Curie process. It does not change
the memory database or stored records.

## Verification

```sh
make memory-kernel-check
.venv/bin/python -m scripts.benchmark_memory_kernel --sizes 1000,10000
```

The blocking native CI lane runs Rustfmt, Clippy with warnings denied, Cargo
unit tests, builds the release extension, then runs parity, owner-isolation,
hierarchical-memory, and unified-memory-service tests with Rust required. The
ordinary unit-test lane runs without building the extension and therefore also
proves that the Python rollback remains healthy.

The benchmark uses deterministic synthetic records and emits only counts and
timings. It reports cold traversal time, warm median time, throughput, and
Rust/Python speedup. Benchmark results are hardware-specific and are not a CI
pass condition; correctness and isolation remain blocking.

On the production server, the 500-candidate workload completed four warm
queries in a median 8.586 ms with Rust versus 31.357 ms with Python: **3.65x
faster**. Separate-process peak RSS was 51,436 KiB versus 53,120 KiB, about
**3.2% lower**, and total user CPU for the benchmark process fell from 0.27 to
0.13 seconds. At deliberately oversized 1,000- and 10,000-candidate workloads,
warm throughput improved by 3.57x and 3.86x respectively. These September 2026
measurements are evidence for this host, not universal guarantees.

## Observability and rollback

`memory_kernel_status()` reports configured mode, module availability, active
backend, ABI kernel version, and whether fallback is allowed.
`retrieval_metrics()` adds native/Python query counts, native failure count,
active kernel status, cache entries, hit rate, scanned candidates, reranked
candidates, selections, and latency. No memory text, owner identifier, query,
or embedding is recorded. General capability health also exposes the kernel;
when `rust` is required, a missing native module degrades readiness instead of
quietly reporting the system healthy. In `auto` mode, the first native runtime
failure opens an in-process circuit breaker and uses Python for later calls.

Rollback is immediate and data-free:

1. set `CURIE_MEMORY_KERNEL=python`;
2. restart Curie;
3. confirm `memory_kernel_status()["active"] == "python"`;
4. run the same retrieval evaluation corpus before investigating the native
   failure offline.

No database downgrade or memory reindex is required because the two kernels
share the same public ranking contract and stored schema.
