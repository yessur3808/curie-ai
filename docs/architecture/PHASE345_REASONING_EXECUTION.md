# Curie Phase 3–5 reasoning and execution architecture

This document describes the implementation boundary added by Phases 3, 4,
and 5. It is deliberately concrete: the goal is for a future maintainer to be
able to trace a Telegram sentence from evidence through a verified outcome
without relying on model intuition.

## End-to-end flow

```text
inbound event
  -> normalized TurnState (owner, connector, request-key hashes)
  -> deterministic evidence recognizers
  -> schema-constrained classifier only when needed
  -> risk-aware clarification / conservative decomposition
  -> canonical capability and entity resolution
  -> validated dependency-graph plan
  -> owner + connector + plan + step + parameter authorization
  -> idempotent execution / bounded retry / cancellation
  -> postcondition verification
  -> truthful aggregate response and persistent receipt
```

Model output can propose an intent. It cannot invent a capability, bypass
authorization, directly name an executor, or prove that a mutation happened.
Those decisions remain deterministic and typed.

## Phase 3: understanding and routing

### Versioned taxonomy

`agent/understanding/taxonomy.py` defines the complete intent-leaf set and its
version. Every leaf includes its definition, required entities, risk class,
candidate capabilities, clarification policy, and boundary examples covering
positive, near-negative, ambiguous, multi-turn, compound, multilingual, and
adversarial wording.

High-precision recognizers run first and return typed evidence spans rather
than only a label. Approval, rejection, emergency stop, cancellation, memory
correction, device mutation, calculations, conversions, health, and
capability-help phrases use this path. Ordinary mentions such as “the lamp in
that novel” are guarded as non-commands.

When deterministic evidence is insufficient, the fallback classifier receives
only a relevant taxonomy subset and a public capability snapshot. Its response
must satisfy the strict JSON schema, name an allowed leaf and registered
capability, and clear the calibrated confidence threshold. Invalid, unexpected,
or low-confidence output becomes an abstention—not a guessed tool call. The
trace records model name, prompt version, taxonomy version, selected subset,
latency, validity, and confidence without storing hidden reasoning.

### Compound and ambiguous requests

Decomposition is conservative. Explicit sequence words preserve requested
order; references such as “it” can mark a data dependency; plain “and” splits
only when both clauses are independently meaningful. Prose containing “and”
is kept intact.

Clarification depends on consequence. A mutating action with multiple
plausible targets asks first. A deterministic group such as “all lights” does
not. Safe read-only work may gather useful information before asking. Unknown
authority always blocks mutation.

## Phase 4: canonical devices and entity resolution

### Canonical inventory

All providers map into `CanonicalDevice`. Identity is the pair of provider and
provider device ID, so same-looking IDs from separate providers never merge.
The canonical record contains normalized and display names, type,
capabilities, room/zone, online and power state, controllability, provider
metadata with secret-like fields removed, state schema, provenance, and
timestamps.

`DeviceInventoryService` refreshes providers concurrently, caches cheap reads,
tracks provider failures separately, and marks previously observed devices
unavailable before stale removal. Known owner inventories receive a bounded
best-effort warm-up before connectors start, then refresh lazily at the
configured interval. One failing provider cannot erase successful inventory
from another. Refresh coordination is event-loop-local so Telegram, API, and
other connector threads can safely share the canonical cache.

### Strict resolution order

`DeviceResolver` evaluates targets in this order:

1. canonical ID;
2. provider ID when a provider is explicit;
3. rejected natural-name tombstone (hard stop);
4. exact normalized display name;
5. owner-confirmed alias;
6. deterministic room/type/capability group;
7. strong fuzzy match above the operation-specific threshold and ambiguity
   gap;
8. recent dialogue reference supplied by the dialogue layer;
9. clarification or not-found response.

Groups such as “all lights,” “every lamp,” “both lights,” “online lights,” or
“kitchen lights” expand from capabilities and room metadata. They do not
require a fictional device whose name is “all lights.”

Fuzzy matches never become confirmed aliases automatically. Successful
references may be stored as expiring candidates, while only explicit user
naming creates a confirmed alias. Corrections take effect immediately and are
owner-scoped. In particular, DreamView has no built-in mapping: after “There
is no device called DreamView,” it remains blocked unless the owner explicitly
defines a new alias later.

### Truthful mutation results

The hub skips devices already in the requested state, controls each remaining
device independently, verifies the returned or refreshed state when possible,
and reports per-device results. Aggregate wording distinguishes verified,
already satisfied, failed, unavailable, contradicted, and unverified outcomes;
partial success is never described as total success.

## Phase 5: planning, authorization, and execution

### Safe planner input and dependency graph

`PlannerInput` accepts typed understanding, a public capability snapshot,
sanitized dialogue/memory, owner permissions, and operational constraints. It
rejects secret, token, password, raw-update, and provider-response fields.

Every plan has stable hashes derived from the owner scope, connector, inbound
request key, ordered intents, registered capabilities, parameters, and risk.
Each step carries typed dependencies, expected latency, partial outcomes,
idempotency policy, verification policy, and compensation policy. Construction
rejects missing dependencies and cycles. Only independent read-only steps may
run in parallel.

### Exact authorization

A consequential approval is bound to all of the following:

- owner scope;
- connector;
- plan hash;
- step ID;
- action and exact parameter hash;
- expiry time.

Changing any field invalidates the approval. A Telegram approval cannot be
replayed through another connector, another owner, or a modified request.

### Idempotency, retry, cancellation, and verification

Stable mutation keys are persisted before execution with an atomic reservation.
A completed duplicate returns its sanitized receipt. A concurrent duplicate is
blocked. If the response to a non-idempotent mutation is lost, Curie reports
the uncertainty and does not send the action again.

Retries are bounded and allowed only for safe reads or capabilities whose
contract explicitly marks retry safe. Cancellation is checked before each
step and between attempts. Completed outcomes remain in the result, dependent
steps do not run after failure, and cleanup callbacks always run.

“Emergency stop,” “stop everything,” and equivalent high-precision phrases
route without a model or approval to an owner-scoped local control capability.
It signals every active in-process plan and marks the owner's non-terminal
durable tasks cancelled. It does not claim to undo work that already finished.

Every mutating capability declares its verification projection,
consistency-delay budget, contradiction behavior, fallback, idempotency mode,
and compensation policy. “Submitted” and “verified” are separate states.
Compensation is modeled as a new auditable action based on captured pre-state;
it never pretends that the original action did not happen and defaults to a
fresh approval.

## Evaluation and rollout

`tests/test_phase345_reasoning.py` covers boundary behavior at component and
integration level. `python -m evaluation.phase345_suite` is an offline CI gate
for taxonomy coverage, macro intent F1, critical recall, false activation,
unnecessary clarification, compound requests, follow-ups, corrections, group
resolution, fuzzy safety, and stable plan bindings.

Rollout should remain staged. Run shadow/diagnostic comparison first, inspect
clarification and mismatch metrics, then enable active routing. Starting the
daemon is intentionally outside code deployment so a reviewed release can be
installed while Curie remains off.
