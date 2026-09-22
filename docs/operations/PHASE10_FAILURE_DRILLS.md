# Phase 10 failure drills

The Phase 10 drills are deterministic, offline, and isolated from production
accounts, devices, and databases. Run them with:

```bash
python -m evaluation.phase10_hardening_suite
```

The complete release command runs the same drills as blocking gates:

```bash
python -m evaluation.release
```

| Drill | Assigned stage | Expected recovery evidence |
| --- | --- | --- |
| Model unavailable | Execute | Typed unavailable-capability result; bounded fallback; retryable |
| Database locked | Persistence | Lock detected, attempt rolled back, write succeeds after release |
| Corrupt memory DB | Persistence | Integrity failure detected; checked replacement reports `ok` |
| Telegram reconnect | Connector receive | Not-ready state retained until a fresh probe succeeds |
| Smart-home timeout | Verification | Failed and unverified; no success claim |
| Expired OAuth | Execute | Typed authentication-expired result; reauthorization required |
| Disk full | Persistence | Readiness degrades and durable writes are withheld |
| Queue saturation | Execute | Excess reads and same-owner mutations rejected without waiting |
| Process restart during task | Execute | Mutation is verified without replay; reads may retry |
| Duplicate inbound | Connector receive | First event accepted; replay suppressed before execution |
| Credential compromise | Observability | Every tested credential class is removed from the artifact |
| Emergency stop | Execute | All active owner-scoped cancellation events are signalled |

## Recovery procedure

1. Locate the request by trace ID and inspect `stage` and
   `pipeline_stage_statuses`. Do not copy raw messages or credentials into an
   incident record.
2. Compare the stage's p95 and success status with adjacent SLO stages. This
   separates connector, model, tool, verification, and delivery delays.
3. Check `capabilities.backpressure` before restarting anything. Saturation is
   expected to clear when active work finishes; repeated rejection indicates a
   capacity or downstream availability problem.
4. For interrupted mutations, verify external state using an idempotency
   receipt or read-only tool. Never replay an uncertain mutation merely because
   the process restarted.
5. For a suspected credential leak, stop affected integrations, rotate the
   credential at its provider, scan private logs and evaluation artifacts, and
   preserve only redacted evidence.
6. Re-run the individual Phase 10 suite and then the complete release command.
   A failed drill or redaction case blocks release.

## Recorded evidence

The release report stores one privacy-safe record per drill: name, failure,
assigned stage, recovery rule, pass/fail, and structured evidence. It does not
store credential values or raw conversation content. On 2026-09-22 the Phase
10 implementation is expected to pass all twelve drills in the candidate's
server verification environment; the generated report is the authoritative
evidence for the exact commit.
