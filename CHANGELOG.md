# Changelog

## 2026.09-phase11-rollout-stabilization

- Made the typed turn pipeline the default while retaining a named and dated
  legacy rollback path through 2026-10-22.
- Added seven runtime-selectable rollout stages covering offline, shadow,
  owner conversation, selected smart-home devices, connector expansion,
  default-active, and final legacy removal.
- Added an atomic local circuit breaker with nine automatic rollback signals,
  fail-closed invalid-state handling, health visibility, and an operator CLI.
- Added a transparent 45-measurement 9/10 scorecard that distinguishes direct
  offline evidence from production-validation proxies.
- Expanded the release contract from 24 to 32 blocking gates and hardened CI
  with bounded jobs, concurrency cancellation, artifact secret scanning, and a
  single aggregate release-readiness check.

## 2026.09-phase10-operational-hardening

- Added privacy-safe end-to-end traces with stage assignment, route,
  confidence, capability, outcome, verification, feature, model, prompt,
  token, queue, and timing metadata.
- Added independent service objectives for connector receipt,
  acknowledgement, context, routing, model, tool, verification, and delivery
  latency and success.
- Added fail-fast global read and per-owner mutation budgets, complete model,
  attachment, connector, and tool saturation health, and degraded readiness
  during saturation.
- Added recursive credential redaction and offline scanning for private logs,
  evaluation artifacts, Telegram URLs, OAuth queries, provider headers, and
  database URLs.
- Added twelve deterministic failure and recovery drills plus five new
  blocking release gates, bringing the complete offline release contract to 24
  gates.

## 2026.09-phase9-comprehensive-evaluation

- Added a versioned evaluation taxonomy and portable case contract spanning
  understanding, conversation, devices, planning, memory, personality,
  security, connectors, proactive behavior, and production regressions.
- Added deterministic structured graders, human-calibrated subjective grading,
  identical-condition model comparisons, provider simulations, latency
  measurement, and privacy-safe actionable failure records.
- Added nineteen fail-closed release gates with explicit metrics, datasets,
  thresholds, component ownership, and controlled waiver requirements.
- Added one complete offline command that runs tests, reports per-stage and
  per-taxonomy metrics, and blocks a release when any required gate regresses.

## 2026.09-phase8-controlled-learning

- Added five explicit learning levels from expiring session adaptation through
  non-deploying code and skill proposals.
- Replaced automatic implicit preference changes with provenance-backed,
  deduplicated candidates that require offline evaluation and shadow evidence.
- Added opted-in Level 3 canaries, automatic safety and metric rollback,
  versioned adaptive configuration, and owner-commanded rollback.
- Added redacted learning events, authority-expansion rejection, temporary
  natural-language device aliases, user inspection controls, and SQLite
  migration 5.

## 2026.09-phase345-reasoning-execution

- Added a versioned intent taxonomy, deterministic evidence recognizers,
  schema-constrained fallback classification, confidence-aware clarification,
  and conservative compound-request decomposition.
- Added canonical smart-home inventory, explicit alias lifecycle and
  corrections, capability and room groups, conservative fuzzy matching, and
  truthful per-device mutation and verification results.
- Added typed dependency-graph plans, exact plan-bound approvals, persistent
  mutation idempotency receipts, bounded safe retries, cooperative
  cancellation, verification policy, and explicit compensation requests.
- Added offline Phase 3–5 behavioral gates and regression tests for command
  activation, device-resolution safety, and execution guarantees.

## 2026.09-phase1-turn-kernel

- Added a feature-flagged fourteen-stage typed turn pipeline with immutable
  artifacts, stage timeouts, cancellation, typed failures, and side-effect
  boundaries.
- Added active and no-repeat-side-effect shadow adapters around the existing
  `ChatWorkflow`, with privacy-safe route comparisons and stage traces.
- Added truthful connector delivery receipts and explicit deferred-delivery
  semantics for workflow responses.
- Added architecture dependency gates and a contract/provenance/privacy audit.
- Consolidated Google and X OAuth state persistence behind an atomic,
  provider-neutral, single-use service.

## 2026.08-phase10

- Published versioned connector, tool, media, memory, voice, and response-policy contracts.
- Added healthy-by-default capability discovery through chat and API.
- Added recorded, checksummed forward migrations and explicit rollback targets.
- Added machine-enforced release manifests, evaluation deltas, and rollback notes.
- Added Phase 9 independent readiness, resource backpressure, and verified memory recovery.
- Enabled mandatory ClamAV scanning for the Curie production instance.
