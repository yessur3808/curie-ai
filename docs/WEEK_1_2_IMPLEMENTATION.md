# Curie Week 1-2 foundation

This milestone makes Curie's chat behavior measurable and gives every turn a typed,
inspectable understanding state before memory retrieval or model generation.

## What changed

- `TurnState`, `GoalSpec`, `SubGoal`, `EntityReference`, and `ResponseMode` are stable
  contracts under `agent/kernel/`.
- Explicit operational commands take a deterministic fast path before adaptive memory,
  prompt construction, or an LLM call.
- Owner- and connector-scoped dialogue state resolves bounded device references such as
  “it”, “the device”, and “them”. Ordinary uses of “it” are not rewritten.
- An alias-plus-control request such as “Correlate DreamView with AI Sync Box strip and
  turn it off” becomes an ordered two-step goal.
- End-to-end JSONL turn events contain hashes, timings, routing metadata, and outcome
  size. They never contain raw user text, response text, user IDs, or tool parameters.
- Telegram now registers operational slash commands that were previously discarded by
  the command filter.
- CurieEval can assert routing, capability, risk, entities, response mode, memory policy,
  tool order, verification status, latency, personality, and recent-answer repetition.
- A deterministic Home Assistant simulator covers fuzzy aliases, already-satisfied
  state, lag, unavailable devices, accepted-but-unchanged commands, and post-command
  verification.

## Verification

Run the milestone suite:

```bash
python -m evaluation.week12_suite
```

Run focused tests:

```bash
pytest -q tests/test_turn_kernel.py tests/test_home_assistant_simulator.py tests/test_evaluation_runner.py
```

The sanitized failure fixtures are in `evaluation/fixtures/telegram_failures.json`.
They contain no Telegram IDs, timestamps, account identifiers, or verbatim assistant
responses.

## Trace privacy and operation

Turn events default to `~/.curie/turn-events.jsonl`, are mode `0600`, and rotate at
10 MB. Configure `CURIE_TURN_TRACE_PATH` and `CURIE_TURN_TRACE_MAX_BYTES` to change
those values. Tracing is fail-open, so an unavailable trace path never breaks chat.

## Boundary of this milestone

The first two weeks establish understanding, isolation, tracing, and regression gates.
Later milestones can build a richer planner and durable semantic memory on these
contracts without coupling tool execution to free-form model output.
