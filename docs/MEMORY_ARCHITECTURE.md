# Curie memory architecture

Curie uses a Letta/MemGPT-inspired hierarchy, but it does not embed Letta and
should not be described as a drop-in Letta implementation.

## Memory layers

1. **Working context** — recent session turns plus a persistent rolling summary.
   The summary keeps active goals, decisions, constraints, named entities, and
   unresolved requests. It explicitly drops completed or casual side topics and
   is labelled as model-generated background, never as a user instruction.
2. **Core memory** — small, verified identity, preference, routine, and assistant
   setting records. Conflicting values wait for user confirmation.
3. **Episodic memory** — salient user-authored decisions, goals, projects, and
   milestones. Routine chat is not promoted and episodes expire by policy.
4. **Archival memory** — longer-horizon project, relationship, biography, and
   hypothesis records retrieved only when relevant.
5. **Procedural/adaptation memory** — declarative learned skills, explicit
   feedback, routing outcomes, and user-controlled response preferences.
6. **Operational state** — durable tasks, approvals, reminders, and redacted
   audit records. These are not treated as conversational facts.

All durable records are owner scoped. SQLite is the zero-configuration backend;
MongoDB/PostgreSQL-backed repositories remain available for deployed setups.

## Retrieval and context control

- Explicit operational commands bypass conversational recall unless they refer
  to earlier context.
- Recall uses a cheap lexical/fuzzy candidate gate followed by hashed-vector,
  freshness, confidence, importance, and reinforcement scoring.
- Per-tier limits, duplicate suppression, and a strict character budget prevent
  the archive from flooding the prompt.
- Pending conflicts and low-confidence or expired hypotheses are not presented
  as facts.
- Long sessions are incrementally compacted. The prior rolling summary is reused
  until additional turns age out of the verbatim window, avoiding repeated full
  summarization and reducing summary drift.
- Resetting a chat clears its derived working summary without deleting unrelated
  preferences or long-term memory.

## Differences from a full Letta-style runtime

Curie does not yet provide model-editable memory blocks, an agent-visible memory
tool loop, learned dense embeddings, a temporal knowledge graph, or scheduled
offline reflection/consolidation. Curie's current design favors deterministic
gating and user control over broad autonomous memory writes.

## Next upgrades

1. Add a measured retrieval corpus with precision, contradiction, deletion, and
   long-dialogue goal-retention gates.
2. Add optional local dense embeddings and reranking while retaining the current
   cheap gate and deterministic fallback.
3. Consolidate near-duplicate episodes and archive completed projects through a
   previewable maintenance job.
4. Track temporal validity (`valid_from`, `valid_until`) separately from record
   creation time.
5. Add relationship/entity links for multi-hop recall without injecting an
   unrestricted knowledge graph into prompts.
6. Give users a compact memory review UI with confirm, correct, pin, forget,
   pause, export, and provenance controls.
7. Run reflection only on trusted user-authored evidence; model-authored text may
   propose a memory but must never silently become verified truth.
