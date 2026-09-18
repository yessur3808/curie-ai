# Changelog

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
