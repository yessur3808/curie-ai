# Controlled self-learning architecture

Phase 8 gives Curie a bounded way to improve from corrections and outcomes. It
does not train the language model during chat and it does not permit Curie to
silently change code, permissions, or consequential-action policy.

## Learning levels

| Level | Purpose | Live-use rule |
|---|---|---|
| 0 | Current-session response length, topic, constraints, and temporary device aliases | Immediate, owner-and-channel scoped, expires automatically |
| 1 | Directly stated durable preferences | Immediate, versioned, provenance-backed, inspectable, and reversible |
| 2 | Low-risk inferred patterns | Requires independent evidence, offline evaluation, shadow observation, and explicit owner approval |
| 3 | Routing, prompt, response-style, or retrieval optimization | Requires unit, targeted, held-out, adversarial, and full evaluation; shadow traffic; an opted-in owner canary; and monitored promotion |
| 4 | Skill implementation, source patch, migration, or policy proposal | May only reach a non-production staged state after tests, security review, and human approval; this subsystem cannot merge, push, deploy, or execute generated code |

Level 0 state lives in session metadata under
`controlled_session_adaptation_v1`. Each value has its own creation time,
expiry, and provenance event. Expired entries are removed when read. A session
reset and channel boundary prevent temporary references from becoming global
facts.

Level 1 changes use the existing adaptation profile, but every history entry
now identifies its learning level and supporting event IDs. Explicit feedback
stores content hashes and bounded features instead of a raw conversation copy.

## Events and candidates

The event store accepts only the enumerated Phase 8 sources. Event text is
converted into a SHA-256 hash plus structural features such as word count and
whether the message contained a question or negation. Sensitive metadata keys
and secret-like values are redacted. This is enough to deduplicate and audit an
observation without creating a second conversation archive.

Every candidate records:

- the exact proposed behavior and problem;
- owner-scoped supporting event IDs;
- hashed counterexamples;
- affected owners;
- expected metric and minimum improvement;
- risk, expiry, and rollback action;
- automatic rollback threshold; and
- a declarative proposed configuration or non-executable code-change summary.

Candidates are deduplicated by an owner-scoped fingerprint. Rejected and failed
candidates remain stored with their evidence and outcome, but the runtime reads
only configurations whose status is `active`.

## Promotion state machine

```text
insufficient evidence
        |
pending evaluation
        |
unit -> targeted -> held-out -> adversarial -> full
        |                               |
evaluation failed                       +-- any safety regression rejects promotion
        |
shadow -- reviewed disagreement traces
        |
        +-- Level 2: explicit owner approval -> promoted
        +-- Level 3: opted-in canary -> monitored promotion
        +-- Level 4: human tests + security review + approval -> staged only
```

The production package does not import the evaluation package. An offline
runner is injected into `run_offline_evaluation`, which makes bypassing one of
the five named gates impossible while preserving the architecture dependency
rule. A Level 3 configuration is not active during shadow mode. During canary
mode it is limited to the opted-in owner and any safety regression or metric
drop below the candidate threshold triggers automatic rollback.

## Authority boundary

Candidate validation recursively rejects permission, role, authority, admin,
credential, protected-branch, approval-policy, and release-gate changes.
Owner scope must contain exactly one owner. Level 2 configurations are also
ignored by `get_active_configuration(..., consequential=True)`, even after an
owner has approved their low-risk presentation use.

Executable learned recipes continue to re-check the registered tool's current
permissions at invocation. Phase 8 never turns a learned preference into tool
authorization.

## Versioning and rollback

Promoted configurations contain their version, prior version, metric delta,
owner scope, evidence IDs, rollback threshold, and a one-command rollback
string. Activating a new version supersedes the previous one without deleting
it. `/learning rollback <config_key>` marks the current version rolled back and
restores the immediately preceding version. A canary with no previous version
is simply disabled on rollback.

User-facing controls:

- `/learning inspect` — candidates, evaluation evidence, and config versions;
- `/learning events` — redacted learning-event provenance;
- `/learning reject <candidate_id>` — reject while retaining analysis evidence;
- `/learning rollback <config_key>` — restore the prior version.

The learning tables are introduced by SQLite migration 5. They are additive;
rolling the schema back removes only Phase 8 events, candidates, and adaptive
configuration versions.
