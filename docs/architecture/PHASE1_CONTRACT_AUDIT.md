# Phase 1 contract audit and turn-kernel architecture

Status: implemented and default-active after Phase 11 stabilization. The
temporary legacy path is an owned, dated circuit-breaker until 2026-10-22.

This document records the Phase 1 boundary decisions for Curie's typed turn
kernel. It is intentionally narrower than the long-term architecture plan: the
new pipeline owns orchestration and trace semantics while proven behavior still
runs through a temporary legacy execution adapter.

## Canonical contract owners

| Concept | Canonical owner | Previous or duplicate concepts | Phase 1 adapter |
|---|---|---|---|
| Whole-turn lifecycle | `agent.kernel.pipeline.PipelineState` | Local dictionaries passed through `ChatWorkflow`; `RequestTrace` timings | `LegacyTurnPipelineAdapter` carries legacy output as a stage artifact |
| Stage outcome | `agent.kernel.pipeline.StageResult` | Ad hoc timing keys and exception strings | Every stage emits status, duration, effects, safe summary, and typed error |
| Pipeline failure | `agent.kernel.errors.PipelineError` | Free-form exceptions and user-facing error strings | Exceptions are classified without serializing exception messages |
| User goal and subgoals | `agent.kernel.contracts.GoalSpec` and `SubGoal` | Router intent strings and command-specific parameter dictionaries | `analyze_turn` produces the canonical typed goal before execution |
| Entity reference | `agent.kernel.contracts.EntityReference` | Regex captures, recent device names, and provider target strings | `DialogueStateStore.resolve_references` resolves before analysis |
| Dialogue reference state | `agent.kernel.dialogue_state.DialogueStateStore` | Session-history inference and `_dialogue_entities` response metadata | The adapter uses the store; provider-resolved device names are observed after execution |
| Understanding | `agent.kernel.understanding.TurnAnalysis` | `intent_router` request and routing-service decisions | Understanding preserves the legacy operational decisions until Phase 2 routing work |
| Execution plan | `agent.kernel.planning.ExecutionPlan` | Inline loops and compound-routing branches | A prebuilt plan is passed to the legacy core as `_execution_plan` |
| Step outcome | `agent.kernel.planning.StepOutcome` | `ResponseCandidate` metadata and tool-specific response dictionaries | `PlanExecutor` converts candidate results into ordered outcomes |
| Capability response | `agent.orchestration.contracts.ResponseCandidate` | Tuples and plain response strings | Used only at the execution boundary; it is not the whole-turn state |
| Response mode | `agent.kernel.contracts.ResponseMode` | Response-planner strings and command-specific acknowledgements | `TurnAnalysis` selects it and the legacy response policy renders the text |
| Connector delivery result | `connectors.lifecycle.DeliveryReceipt` | Boolean return values and raised provider exceptions | `send_with_receipt` records delivered/rejected/not-ready/failed truthfully |
| Privacy-safe trace | `PipelineState.as_dict` plus `TurnEventWriter` | `RequestTrace` and unstructured log messages | Only allowlisted hashes, counts, classifications, timings, and statuses are emitted |
| Rollout decision | `agent.kernel.feature_flags.PipelineFeatureFlags` | No whole-turn path flag | Owner, connector, default, or explicit test override selects legacy/shadow/active |

The canonical contracts do not make provider-specific facts canonical. For
example, a smart-home provider receipt remains provider data until an execution
or verification adapter converts it into a typed outcome.

## Fixed stage contract

The runtime contract is available through `contracts.catalog.contract_catalog`
and lists input/output artifact names, timeout, cancellation semantics, allowed
effects, failure behavior, trace fields, and legacy-fallback policy.

| Stage | Input artifact | Output artifact | Effects permitted | Failure behavior |
|---|---|---|---|---|
| normalize | `NormalizedInput` | `CanonicalTurnInput` | none | stop |
| resolve identity | `CanonicalTurnInput` | `OwnerScopedTurn` | identity write | stop |
| build context | `OwnerScopedTurn` | `ContextEnvelope` | storage read | degrade |
| understand | `ContextEnvelope` | `TurnAnalysis` | none | stop |
| route | `TurnAnalysis` | `RoutingDecision` | none | stop |
| plan | `RoutingDecision` | `ExecutionPlan` | none | stop |
| authorize | `ExecutionPlan` | `AuthorizationDecision` | approval control | stop |
| execute | `AuthorizationDecision` | `ExecutionResult` | tool read/mutation and temporary legacy bridge effects | stop |
| verify | `ExecutionResult` | `VerifiedOutcome` | tool read | degrade |
| plan response | `VerifiedOutcome` | `ResponsePlan` | none | stop |
| render response | `ResponsePlan` | `RenderedResponse` | none | minimal fallback |
| deliver | `RenderedResponse` | `DeliveryReceipt` | delivery | stop |
| persist | `DeliveryReceipt` | `PersistenceReceipt` | persistence | degrade |
| record learning | `PersistenceReceipt` | `LearningReceipt` | learning | degrade |

The runner rejects a declared effect outside the stage allowlist. A mutating
tool effect is invalid unless an authorization stage has already completed.
Cancellation is checked before every stage, so cancellation after authorization
cannot enter execution. A task-level `asyncio.CancelledError` is propagated and
is never converted into an ordinary pipeline failure.

Phase 1 delivery is a connector boundary: the workflow's delivery stage reports
`deferred_to_connector`, and the connector produces the final `DeliveryReceipt`.
This avoids claiming delivery merely because response rendering succeeded.

## Field provenance and privacy classification

| Field | Source | Classification | Serialized form |
|---|---|---|---|
| `trace_id` | Connector when supplied, otherwise pipeline-generated UUID | operational | raw random identifier |
| `turn_id` | Pipeline-generated UUID | operational | raw random identifier |
| platform | Connector normalization | operational | allowlisted connector name |
| internal/external user and chat IDs | Connector and identity service | personal/restricted | never serialized; one-way owner hash only |
| raw message and resolved text | Connector and dialogue resolver | personal | never serialized; SHA-256 message hash and character count only |
| credentials, tokens, headers, cookies | Environment/provider adapters | secret | forbidden from artifacts intended for traces; structured and token-like values are redacted defensively |
| entities and tool parameters | Understanding/router | personal, sometimes sensitive | counts and entity kinds only; raw values stay in in-memory artifacts |
| route and capability names | Understanding/router | operational | allowlisted names and counts |
| execution/provider output | Capability adapters | potentially sensitive | result artifact stays in memory; traces contain status and verification category only |
| response text | Renderer | personal | never written by turn traces; character count only |
| exception message | Any stage | potentially secret | never serialized; exception class and typed error category only |
| stage timing/status | Pipeline runner | operational | serialized |
| shadow comparison | Pipeline adapter | operational | booleans and capability counts only |

`PipelineState` freezes container artifacts recursively. A later stage receives a
thawed copy through the adapter and cannot mutate an earlier stage's stored
artifact. `PipelineState.as_dict()` excludes both seed input and artifacts. Safe
summaries are still passed through key-based and token-shaped redaction as a
second line of defense.

## Dependency direction

The architecture tests enforce these boundaries:

1. Connectors may use the public memory facade and session service, but not
   provider-specific repositories, schemas, or local database implementations.
2. Response planning and rendering may not import connectors, services, or
   mutating capability implementations.
3. Memory modules may not import `ChatWorkflow`.
4. Capability implementations may not import Telegram SDK or Telegram connector
   types.
5. Production modules may not import evaluation packages.
6. The turn kernel may not depend on a connector.

These are import boundaries, not merely conventions; CI fails when they are
violated.

## Legacy bridge and migration limits

`LegacyTurnPipelineAdapter` is the only intentional Phase 1 bridge. In active
mode it performs normalization, identity, context, understanding, routing,
planning, and authorization as explicit stages, then calls
`ChatWorkflow._process_message_core` once inside the execute stage. Existing
persistence and guarded-learning behavior still occurs inside that call, so the
later pipeline stages truthfully report `handled_by_legacy_bridge` rather than
pretending those responsibilities have already been extracted.

The bridge must not grow new behavior. Later phases replace one responsibility
at a time behind the same stage contract. Once persistence, learning, response
rendering, and delivery have native handlers, their legacy effect declarations
can be removed.

## Rollout and rollback

Environment variables:

- `CURIE_TURN_PIPELINE_STAGE=offline|shadow|conversation_canary|smart_home_canary|connector_expansion|default_active|legacy_removed`
- `CURIE_TURN_PIPELINE_MODE=legacy|shadow|active`
- `CURIE_TURN_PIPELINE_ACTIVE_OWNERS=owner-a,owner-b`
- `CURIE_TURN_PIPELINE_SHADOW_OWNERS=owner-c`
- `CURIE_TURN_PIPELINE_ACTIVE_CONNECTORS=api`
- `CURIE_TURN_PIPELINE_SHADOW_CONNECTORS=telegram`
- `CURIE_TURN_PIPELINE_DEVICE_CANARY=Floor Lamp`
- `CURIE_PIPELINE_ROLLOUT_STATE=~/.curie/pipeline-rollout.json`

Shadow holds take precedence over active rollout. An explicit internal
`_pipeline_mode` requires an authorization marker and is reserved for tests and
operator controls. The default is `active`; the atomic runtime circuit breaker
changes subsequent turns to legacy without a restart.

Shadow mode runs the legacy path once and keeps its response authoritative. The
typed pipeline reuses that execution result and therefore does not repeat model
generation or external tool mutations. It records privacy-safe route agreement,
capability counts, stage statuses, and stage durations.

Active mode runs the same behavior through the typed lifecycle. The execute
stage currently delegates to the legacy core once. Every result identifies its
origin, and every active pipeline result includes the redacted stage trace.

## Phase 1 validation matrix

- Complete text turn: all fourteen stages appear in canonical order.
- Deterministic device command: no conversation model or conversational memory
  load occurs.
- Context-store failure: context is degraded and later stages continue safely.
- Cancellation: execute and every later stage remain unentered/skipped.
- Rendering failure: verified text/result survives through a minimal fallback.
- Illegal render effect: tool mutation is rejected at the rendering boundary.
- Connector failure: receipt is `failed` and `delivered` is false.
- Serialization: raw IDs, raw text, artifacts, exception messages, and injected
  token values do not appear.
- Shadow rollout: the legacy response remains authoritative and route agreement
  is reported.
- Architecture direction: all six import gates pass.

The typed pipeline is now the default. The legacy path has an explicit owner,
rollback revision, and removal deadline; `legacy_removed` rejects even an
authorized per-turn legacy override. Production observations must still
replace the scorecard's explicitly labeled offline proxies before final legacy
removal.
