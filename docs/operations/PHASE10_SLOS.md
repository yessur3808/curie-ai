# Phase 10 service objectives and capacity controls

Curie records bounded, in-process service-level indicators for each material
stage of a request. The dashboard is available under
`capabilities.slos` in the health payload and is also written to the private
telemetry snapshot. Each stage has an independent latency and success result,
so a slow model is distinguishable from a slow tool or connector.

| Stage | Default p95 target | Default success target | Primary signal |
| --- | ---: | ---: | --- |
| Connector receive | 250 ms | 99% | Normalize inbound event |
| Acknowledgement | 1,000 ms | 99% | Resolve owner and accept work |
| Context build | 1,500 ms | 99% | Assemble bounded conversation context |
| Routing | 500 ms | 99% | Understand and select a capability |
| Model generation | 20,000 ms | 97% | Inference, excluding queue wait |
| Tool execution | 30,000 ms | 97% | Capability invocation |
| Verification | 5,000 ms | 99% | Read-back and evidence checks |
| Connector delivery | 3,000 ms | 99% | Send the final connector response |

Each entry reports count, p50, p95, its target, success rate, and one of
`no_data`, `meeting`, or `breached`. A stage with no observations is not
reported as healthy. Override a latency target with
`SLO_<STAGE>_P95_MS`, such as `SLO_MODEL_GENERATION_P95_MS=15000`.

## Backpressure

Curie rejects excess work instead of allowing unbounded memory growth:

- Model inference uses a bounded priority queue and exposes depth, capacity,
  utilization, and saturation.
- Attachment processing uses a bounded fail-fast slot pool.
- Read-only tools share a process-wide limit controlled by
  `GLOBAL_READ_CONCURRENCY_LIMIT` (default 16).
- Mutating tools have a per-owner limit controlled by
  `OWNER_MUTATION_CONCURRENCY_LIMIT` (default 1). Different owners do not
  block one another within the global tool policy.
- Connector delivery uses a bounded queue and counts rejected submissions.

The health payload groups these signals under `capabilities.backpressure`.
`/health` displays `Capacity: Available` or `Capacity: Saturated`; saturation
degrades readiness until capacity returns.

## Trace privacy contract

Pipeline events include trace and turn IDs, a pseudonymous owner hash,
connector, failed or completed stage, duration, route, confidence, capability,
outcome and verification categories, feature flags, model and prompt versions,
token counts, queue wait, stage statuses, stage durations, and an SLO snapshot.

Events never intentionally include raw user messages, assistant messages,
tool parameters, attachment content, access tokens, bot tokens, passwords,
provider authorization headers, credential-bearing URLs, or raw owner IDs.
The recursive redactor is the final write boundary for turn events and release
artifacts.

To scan generated artifacts without printing their contents:

```bash
python scripts/scan_runtime_artifacts.py \
  ~/.curie/turn-events.jsonl evaluation/reports
```

The command reports only paths and detector categories and exits non-zero when
a credential shape remains.
