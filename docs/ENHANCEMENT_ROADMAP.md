# Curie Enhancement Roadmap

Last audited: 2026-08-24

This is the canonical plan for improving Curie. It replaces phase-completion
claims with evidence, measurable release gates, and a prioritized backlog.
Passing unit tests means the tested contracts work; it does not prove that a
real model, every connector, or every external provider works in production.

## Product outcome

Curie should be a dependable general assistant that:

- answers ordinary conversation naturally without forcing every message into a tool workflow;
- identifies goals, constraints, entities, urgency, risk, and missing details;
- combines compatible intents and asks one focused question only when ambiguity matters;
- chooses live retrieval, deterministic computation, memory, or model reasoning appropriately;
- executes authorized actions through typed, least-privilege adapters;
- verifies externally visible outcomes and explains partial failure or fallback;
- preserves context and user identity safely across supported channels;
- advertises only capabilities that are configured and healthy.

Maximum usefulness does not mean maximum autonomy. Consequential actions remain
preview-first, owner-scoped, auditable, and subject to fresh approval.

## Audit snapshot

The 2026-08-24 audit found a substantial implemented foundation, but not a
finished product.

| Area | Evidence | Assessment |
| --- | --- | --- |
| Automated correctness | `1169 passed, 2 deselected` in the project virtual environment | Strong local foundation; network, hardware, model, and live-connector tests are excluded by default |
| Unified chat routing | One typed `RoutingDecision` feeds social, system, specialist, tool, approval, and conversation paths | Implemented; intent breadth and compound requests need stronger evaluation |
| General chat fallback | Unmatched chat and conversational uses of capability words fall through to the conversation model | Implemented and regression-tested |
| Tool contracts | Registry validates schemas, availability, permissions, timeouts, concurrency, and output size | Foundation; metadata and routing rules remain duplicated |
| External actions | Owner approvals, audits, task graphs, retries, and verification exist | Partial; third-party workflows are adapter/configuration gated and some code-host paths are unsupported |
| Messaging | API, Telegram, Discord, Slack, and WhatsApp share chat/media contracts | Contract-tested; credentialed delivery and reconnection are not proven by the default suite |
| Media and voice | Bounded ingestion, OCR/transcription/voice, progress, and fallback have tests | Foundation; quality depends on local models, binaries, and connector limits |
| Memory | Typed owner-scoped stores, correction, retention, provenance, and adaptation exist | Strong foundation; retrieval quality and long-horizon staleness need measured datasets |
| Security and resilience | Backpressure, health, backup/restore, URL/file controls, audits, and release evidence exist | Strong foundation; live incident exercises and runtime matrices remain |
| Maintainability | Orchestration services have been extracted | Incomplete: `chat_workflow.py` is 1,472 lines, several modules exceed 800 lines, and lint reports 116 violations |
| Compatibility | The test run emits 929 warnings, mainly Python 3.14 asyncio and naive-UTC deprecations | Cleanup required before warnings become failures |
| Release tooling | Evidence validates with `PYTHONPATH=.` | Fix required: direct `python scripts/check_release.py` cannot import `contracts` |

### Claims this roadmap does not make

- “All intents are covered” is not measurable without a versioned taxonomy and representative utterance set.
- An importable connector does not prove authentication, webhook delivery, rate-limit recovery, media upload, or reconnection.
- Mocked provider tests do not establish live factual accuracy or availability.
- Optional voice, vision, and model features are unavailable when dependencies or model files are unhealthy.

## Release scorecard

Every release must publish these results by hardware profile and connector:

- intent top-1 accuracy, false-tool-trigger rate, clarification rate, and per-intent recall;
- compound-request decomposition and completion rate;
- ordinary-chat direct-answer rate and unnecessary-question rate;
- factual support, citation validity, numerical accuracy, and stale-data rate;
- tool selection, validation, execution, verification, rollback, and recovery rates;
- memory precision, contradiction rate, correction latency, and deletion proof;
- connector receive/send, dedupe, reconnect, media, and fallback success rates;
- p50/p95 latency and queue saturation by stage;
- approval bypass, cross-owner access, SSRF, traversal, replay, and prompt-injection failures;
- lint/type errors, deprecation warnings, flaky tests, and module complexity.

No engagement metric may reward keeping a user in conversation.

## Priority 0: Restore a truthful green baseline

**Goal:** Make every claimed quality gate runnable, quiet, and reproducible.

- Fix direct invocation of release tooling without an implicit `PYTHONPATH` workaround.
- Reduce lint failures from 116 to zero, beginning with undefined `_ProgressHandle`, unused imports, compressed control flow, and ambiguous names.
- Replace naive `datetime.utcnow()` calls with timezone-aware UTC values.
- Remove Python 3.14 asyncio-policy deprecations or document a supported interpreter matrix until dependencies are compatible.
- Make evaluation execution self-describing: provide a sample or a command that generates its required response file.
- Split CI into unit, integration, security, model/hardware, and credentialed connector lanes; show which lanes did not run.
- Exclude generated databases, WAL/session files, caches, and model outputs from source and CI artifacts.

**Exit gates:** tests, lint, and type checks pass; release and evaluation commands
work exactly as documented; no untriaged warnings remain.

## Priority 1: Complete intent understanding for general chat

**Goal:** Understand what the user wants while protecting ordinary conversation
from false tool activation.

Create one versioned taxonomy shared by routing, capabilities, tests, analytics,
and documentation. It must cover:

- conversation: greeting, small talk, explanation, brainstorming, writing, summarization, translation, support, correction, and refusal;
- knowledge: stable fact, current fact, research, comparison, recommendation, calculation, conversion, date/time, and weather;
- personal context: remember, recall, correct, forget, pause learning, preference, identity linking, privacy, and export;
- planning: goal decomposition, schedule, reminder, travel, navigation, task status, cancellation, and resumption;
- media: image, screenshot, document, table, audio, voice response, unsupported type, and oversized input;
- local operations: inspect, test, diagnose, modify, create, delete, and recover;
- external operations: calendar, email/message, files, notes/tasks, code host, deployment, and account operations;
- control: help, capabilities, health, connector/voice/proactive settings, approval, rejection, and emergency stop;
- safety: high-stakes advice, secrets, destructive requests, ambiguous authority, injection, and cross-owner requests.

Then improve the router:

- Represent a request as a goal plus ordered sub-intents instead of asking for ordering for every pair of independent actions.
- Execute compatible read-only sub-intents together, sequence dependencies, preview consequential groups once, and preserve partial results.
- Extract typed constraints with provenance: time zone, deadline, location, target account, platform, format, freshness, and success condition.
- Keep deterministic rules for precise forms; use a schema-constrained classifier for the long tail with calibrated abstention.
- Learn from routing corrections without granting permissions or retaining raw private messages.
- Resolve follow-ups against the prior answer, tool result, attachment, or a new goal.
- Make the runtime registry the only source for capability names, schemas, thresholds, examples, and routing hints.

**Exit gates:** every taxonomy leaf has positive, negative, ambiguous,
adversarial, multilingual, and multi-turn examples; macro F1 is at least 0.95;
no leaf recall is below 0.90; false tool activation is below 0.5%; unnecessary
clarification is below 3%.

## Priority 2: Make capabilities compose end to end

**Goal:** Move safely from understanding through verified completion.

- Use a typed pipeline: normalize input, load context, route, plan, authorize, execute, verify, formulate, persist, observe.
- Give every stage one structured error model.
- Select tools only from the healthy registry; unavailable tools return accurate fallback or setup instructions.
- Add compensating actions when multi-step work cannot truly roll back.
- Carry cancellation, deadlines, idempotency, owner/connector identity, provenance, and trace IDs end to end.
- Verify mutations from the destination rather than equating an accepted API call with completion.
- Preserve successful sub-results and retry only unfinished mutations.

**Exit gates:** end-to-end scenarios cover chat-only, chat-to-tool, multi-tool,
approval, cancellation, timeout, outage, restart, duplicate delivery, and
rollback; every mutation has a destination-verified receipt.

## Priority 3: Strengthen external connectivity

**Goal:** External platforms behave as reliable adapters, not special cases.

- Define one connector conformance kit for identity, text, formatting, attachments, progress, commands, dedupe, retries, rate limits, reconnect, and shutdown.
- Publish a matrix separating implemented, configured, live-verified, degraded, and unsupported states.
- Complete explicit adapters for calendar, email, tasks/notes, cloud files, messaging, code hosting, and deployment before advertising them.
- Use scoped OAuth tokens; validate scopes at startup and before consequential actions.
- Normalize provider errors into retryable, permission, quota, invalid-input, conflict, and permanent categories.
- Add signature validation, replay protection, durable cursors, jittered backoff, circuit breakers, and dead-letter inspection.
- Test credential rotation and revocation without restart.
- Add opt-in live synthetic checks with dedicated test accounts and harmless resources.

**Exit gates:** each advertised integration passes its live conformance lane;
scope loss and rate limits degrade clearly; replay tests create zero duplicate
mutations; unsupported adapters are not shown as available.

## Priority 4: Improve answer accuracy and freshness

**Goal:** Choose the right evidence path and communicate uncertainty clearly.

- Route arithmetic, conversion, dates, durations, and conflicts to deterministic utilities.
- Require live retrieval for changing claims with source, retrieval time, geographic scope, and freshness.
- Validate that citations support each generated claim, not merely the topic.
- Distinguish observation, retrieval, user fact, memory, model knowledge, and inference in provenance.
- Detect contradictions across input, history, memory, documents, and tool results.
- Independently verify high-stakes/high-impact outputs and state limitations when authoritative data is unavailable.
- Build domain packs for time, weather, travel, software, finance, health, security, and local-system state.

**Exit gates:** deterministic suites are exact; citation entailment and freshness
meet thresholds; unsupported confident claims are below 1%; high-stakes answers
never omit source and uncertainty requirements.

## Priority 5: Memory and natural conversation

**Goal:** Personalization improves relevance without corrupting truth or privacy.

- Separate durable facts, preferences, temporary context, routines, summaries, and relationship style.
- Rank memories by relevance, recency, confidence, confirmation, and scope; never inject unrelated profile data as fallback.
- Reconfirm stale or contradictory memories before acting.
- Make remember/forget/correct/provenance/export/pause commands identical across connectors.
- Evaluate long conversations for goal retention, references, correction, topic changes, and false-memory resistance.
- Keep warmth, French usage, affection, verbosity, and proactivity explicit and editable; urgency overrides decoration.
- Enforce boundaries deterministically: no guilt, dependency, exclusivity, jealousy, manipulation, or claims of human needs.

**Exit gates:** memory precision is at least 0.98; deletion is immediately
verifiable; stale memories never drive consequential actions without
confirmation; human review meets directness and naturalness targets.

## Priority 6: Media and voice parity

**Goal:** Images, documents, audio, and voice share the text conversation pipeline.

- Persist resumable attachment jobs with expiry where synchronous completion is impossible.
- Add layout-aware OCR, tables, page/chunk selection, timestamps, and image regions to provenance.
- Reject unsupported, encrypted, corrupted, malicious, and oversized media before expensive work.
- Benchmark text, vision, OCR, transcription, and speech per supported hardware profile.
- Stream voice where supported and always retain the complete text answer.
- Apply identical media semantics and errors across connectors.

**Exit gates:** media conformance passes; observations trace to page/time/region;
overload cannot starve text chat; speech failure always returns complete text.

## Priority 7: Refactor for efficiency and maintainability

**Goal:** Reduce duplication, import coupling, and oversized modules without
changing behavior.

- Reduce `agent/chat_workflow.py` to composition/lifecycle; extract prompt assembly, persistence, cache ownership, command dispatch, and finalization.
- Split `llm/manager.py` into inventory, selection, loading, generation, fallback, and lifecycle.
- Split connectors into transport, identity, media, command, and delivery adapters.
- Consolidate overlapping memory stores and document the owner of profiles, conversations, sessions, learned skills, adaptation, and audits.
- Remove legacy routing paths after parity tests prove the unified path.
- Replace broad exception swallowing with typed errors, targeted recovery, and suppression counters.
- Move blocking I/O off the event loop; bound executors/queues; minimize global mutable singletons.
- Add import-cycle checks, complexity budgets, architecture tests, and ownership documentation.

**Exit gates:** no production module exceeds the agreed complexity budget without
an exception; lint/type checks are clean; duplicates are removed; latency and
memory use do not regress.

## Priority 8: Security, privacy, and operations

**Goal:** Expanding connectivity never expands invisible authority.

- Complete encryption and key recovery for sensitive local records and backups.
- Test SSRF, DNS rebinding, traversal/link attacks, archive expansion, malicious media, injection, replay, approval theft, and cross-owner access.
- Redact credentials and sensitive payloads from logs, traces, audits, errors, caches, and prompts.
- Define retention/deletion for messages, media, transcripts, speech, routing outcomes, approvals, tasks, and events.
- Add SLO dashboards for availability, latency, queue depth, tool failures, connector lag, and model fallback.
- Exercise backup/restore, corruption, connector outage, credential compromise, and emergency stop in scheduled drills.
- Publish upgrade, migration, rollback, and compatibility evidence per release.

**Exit gates:** security lanes block release; drills produce tested recovery
times; deletion/export cover every store; supported SLOs are met.

## Definition of done for any capability

A capability is “implemented” only when all are true:

1. It has a typed contract, risk, owner scope, permission model, availability probe, and truthful description.
2. Its intents have positive, negative, ambiguous, multi-turn, multilingual, and adversarial examples.
3. Unit, integration, security, failure, and connector-conformance tests pass.
4. Consequential actions have preview, fresh approval, idempotency, destination verification, audit, cancellation, and recovery.
5. Missing dependencies, scope loss, timeout, rate limit, restart, and partial failure have tested behavior.
6. Accuracy, latency, privacy, and resource metrics meet published thresholds.
7. Setup, limitations, retention, migration, rollback, and ownership are documented canonically.
8. It passes a live synthetic check on every platform where advertised.

Until all eight pass, status is **foundation**, **partial**, or **experimental**,
never simply **implemented**.
