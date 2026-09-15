# Curie Week 3-4 planning and verified execution

This milestone builds the compositional planning and execution layers on the typed
turn foundation from Weeks 1-2. Operational requests no longer depend on a loose
sequence of free-form handler calls.

## Week 3: planning and reasoning

- `GoalConstraint` records typed constraints and provenance while keeping values out
  of privacy-safe traces by default.
- Compound requests are decomposed only when every clause is independently and
  deterministically routable. This prevents ordinary prose and device lists from
  becoming accidental commands.
- Explicit sequence words create dependencies. Independent read-only work may run in
  parallel, while mutations always stay ordered.
- `ExecutionPlan` and `PlanStep` record risk, availability, dependency order, and the
  approval strategy before any tool runs.
- The runtime registry is checked while planning so an unavailable capability has a
  truthful typed outcome instead of being silently sent to a model.

## Week 4: execution, verification, and delivery

- `PlanExecutor` returns one `StepOutcome` per planned action and one aggregate
  `PlanExecutionResult` for the turn.
- Completed sub-results survive a later failure. Dependent steps are skipped rather
  than run against invalid assumptions.
- Tool results carry status, retryability, source, typed errors, and destination
  verification independently of user-facing prose.
- Smart-home mutations distinguish verified, already satisfied, contradicted,
  unverified, and failed outcomes from device receipts.
- Chat results now expose plan, execution status, verification status, and deliberate
  message parts. Telegram sends distinct parts as separate rich-text messages.
- Curie's prompt gets a current-turn contract that makes the newest goal outrank stale
  history and allows at most one grounded proactive recommendation.
- Curie's persona now combines direct command handling, restrained banter, situational
  awareness, scientific curiosity, and a light French identity without imitating
  another fictional assistant's dialogue or catchphrases.

## Verification

Run the focused suite:

```bash
pytest -q \
  tests/test_planning_execution.py \
  tests/test_turn_kernel.py \
  tests/test_action_router.py \
  tests/test_telegram_helpers.py
```

Run the milestone evaluation:

```bash
python -m evaluation.week34_suite
```

Then run the complete default suite:

```bash
pytest -q
```

Live Telegram verification is deliberately separate because it requires a configured
bot, real connector delivery, the active model stack, and any selected smart-home
providers. A live mutation is complete only when its destination reports the requested
state.

## Live Telegram acceptance coverage

The desktop acceptance pass covers the user-facing paths that automated evaluation
cannot prove by itself:

- health and hardware status render as clean headings and lists rather than dense text
  or raw JSON;
- independent read-only requests return as deliberate separate messages;
- natural arithmetic takes the fast deterministic path;
- a product-family phrase such as DreamView can resolve to the unambiguously matching
  AI Sync Box while confirmations retain the provider's real device name;
- power commands report verified completion and already-satisfied state separately;
- explicit recommendations make a choice instead of returning the decision to the
  user;
- explicit formatting previews render bold, italic, strikethrough, underline, links,
  bullets, and compact tables in Telegram;
- short banter and routine technical wins use bounded fast paths so they stay relevant,
  brief, and resistant to history repetition; and
- slower local-model replies keep Telegram's typing indicator alive until delivery.

## Remaining boundary

This is a strong deterministic planning foundation, not unrestricted autonomous
reasoning. Long-tail compound requests still abstain or use the schema-constrained
classifier, consequential tools retain their approval policies, and a provider outage
cannot be converted into a success by language generation. Later milestones should add
durable checkpoints, cancellation propagation, idempotency across process restarts,
compensating actions, and a larger human-scored conversation corpus.
