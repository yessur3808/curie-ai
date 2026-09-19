# Phase 2: Input, dialogue, and context architecture

## Status

Implemented in the Phase 2 context/dialogue change set. The public connector
dictionary remains compatible, but every turn is converted to typed contracts
before identity resolution, memory retrieval, routing, or inference.

## Goals

Phase 2 removes four sources of conversational drift:

1. Connector-specific input differences.
2. Implicit conversational state inferred from an undifferentiated transcript.
3. Unbounded prompt assembly and unexplained memory injection.
4. Rolling summaries that do not identify their source range or inferences.

The current user message always has the highest authority. An explicit
correction, rejection, cancellation, approval, or denial is represented as a
transition instead of being left for a language model to infer.

## Canonical inbound events

`agent.kernel.inbound.InboundEvent` is the single input contract for Telegram,
the API and WebSocket surfaces, Discord, Slack, WhatsApp, Teams, and other
connectors that call `ChatWorkflow`.

It contains:

- connector and connector-account identity;
- external user, chat, and message identity;
- exact original text;
- normalized parser text;
- UTC timestamp and optional edit timestamp;
- a typed event kind;
- typed attachment descriptors;
- the already-resolved internal owner ID when a connector has one; and
- a dedupe key scoped by connector account, chat, message, and edit revision.

Normalization uses Unicode NFC, canonical line endings, conservative whitespace
cleanup, and a small punctuation translation table. It does not lowercase text
or remove diacritics. The exact connector text remains available separately for
citations and exact references.

### Input bounds

Validation happens before identity writes, database reads, or model calls.
Defaults can be changed through environment variables:

| Setting | Default | Purpose |
|---|---:|---|
| `CURIE_MAX_MESSAGE_CHARS` | 32,000 | Maximum Unicode characters |
| `CURIE_MAX_MESSAGE_BYTES` | 131,072 | Maximum UTF-8 bytes |
| `CURIE_MAX_ATTACHMENTS` | 12 | Maximum attachment descriptors |
| `CURIE_MAX_ATTACHMENT_BYTES` | 25 MiB | Maximum known size for one attachment |
| `CURIE_MAX_TOTAL_ATTACHMENT_BYTES` | 50 MiB | Maximum known total attachment size |

NUL-containing, malformed, or oversized inputs are rejected or quarantined with
a safe error code. Their raw body is not written to logs.

### Edited-message policy

The default is `process_revision`. The original event and every authenticated,
timestamped revision receive separate dedupe identities and each revision is
processed no more than once. A connector may explicitly select `ignore` when it
cannot authenticate or order edit events. An edited event without a revision
timestamp is quarantined instead of being guessed into sequence.

## Dialogue state

`DialogueStateStore` holds an owner-and-connector-scoped `DialogueState`. Each
non-empty state field is a `StateValue` with:

- source turn;
- creation time;
- expiry time;
- confidence;
- hashed owner scope; and
- source class.

The state explicitly represents:

- active goal;
- active topic;
- last completed goal;
- last failed goal;
- unresolved assistant question;
- expected answer type;
- current referenced entity set;
- last verified tool result;
- pending approval;
- pending task;
- topic-rejection markers;
- last proactive message; and
- whether that proactive message was answered.

State is deliberately bounded and expiring. It is not a replacement for durable
personal memory. Device references normally expire after 30 minutes, unresolved
questions after 15 minutes, active topics and verified outcomes after one hour,
and pending tasks or approvals after one day.

### Transition precedence

The deterministic transition classes are:

1. continuation;
2. direct answer;
3. correction;
4. rejection;
5. new goal;
6. interruption;
7. cancellation;
8. approval or denial;
9. unrelated small talk; and
10. connector command.

Classification applies these authority rules:

- an explicit correction outranks dialogue state and durable memory;
- a completed goal is not automatically copied into a new topic;
- a rejected topic is temporarily suppressed from prompt context;
- device pronouns resolve only against the latest relevant device entity set;
- an unrelated assistant or user noun cannot replace that entity set;
- a bare “yes” or “no” is an approval decision only while an approval is pending;
- an unanswered proactive message cannot be stacked with another one; and
- a fresh explicit request outranks both pending proactive context and older
  conversational context.

Tool-returned device names are preferred over a vague user phrase such as “all
lights.” Therefore, after a verified tool lists two lights, “turn both devices
on” targets those two identities. A correction such as “there is no device
called DreamView” invalidates that reference rather than allowing “it” to revive
the nonexistent name later.

## Context budgeter

`ContextBudgeter` creates a `ContextEnvelope`. It records an inclusion or
exclusion decision for every candidate without serializing the candidate's
private content into diagnostics.

Default section allocations are:

| Section | Tokens |
|---|---:|
| Current message | untruncated |
| Unresolved goal/question | 384 |
| Recent verbatim turns | 1,600 |
| Rolling summary | 600 |
| Durable memories and approved abilities | 800 |
| Tool schemas | 1,000 |
| Tool results | 1,200 |
| Persona contract | 1,800 |

The default overall dynamic-context budget is 7,600 approximate tokens and can
be changed with `CURIE_CONTEXT_TOKEN_BUDGET`.

The following are invariants:

- the current request is never truncated;
- consequential tool evidence is included atomically or not represented as a
  success;
- contradicted and explicitly rejected facts are excluded;
- a self-contained operational device command does not retrieve personal
  memory;
- recent history is selected newest-first but restored to chronological prompt
  order; and
- every selection records a human-readable reason and estimated token count.

Normal conversation results expose a privacy-safe `context_selection` trace.
It contains IDs, section names, decisions, reasons, and counts, never message or
memory content.

## Rolling summaries

The existing `working_context_v1` storage key remains compatible. Its record now
also includes schema 2 metadata:

- first and last covered fingerprints;
- a deterministic covered-turn count;
- a source-turn range;
- `generated_inference: true`; and
- explicit user corrections with source fingerprints.

Only newly aged turns are sent to the summarizer. The prompt preserves active
goals, constraints, explicit decisions, named entities, and unresolved
questions while dropping completed casual topics and repeated assistant text.
Explicit corrections are supplied as authoritative input. A deterministic
post-pass removes conflicting inferred sentences and appends the correction so
a model cannot silently restore an invalidated claim.

The prompt-visible summary is labeled as model-generated background inference,
not as a user instruction.

## Connector behavior

Connectors still own transport concerns. Media-capable connectors now provide
attachment ID, kind, filename, content type, known size, and source metadata.
No file bytes or temporary paths enter the event contract. Slack's existing
policy of ignoring unauthenticated `message_changed` events remains explicit at
the connector edge; authenticated revisions can use the shared revision policy.

## Verification gate

`tests/test_phase2_context_dialogue.py` covers input validation, all principal
transition classes, device-reference behavior, topic rejection, proactive
stacking, context budgets, and summary corrections.

`python -m evaluation.phase2_suite` covers the required multi-turn release
scenarios:

- device control followed by a work topic;
- correction of a nonexistent device;
- “both” after two verified devices;
- “it” after unrelated social wording;
- “yes” with a real pending approval;
- a fresh request while another task is pending; and
- rejection of one project topic followed by discussion of another project.
