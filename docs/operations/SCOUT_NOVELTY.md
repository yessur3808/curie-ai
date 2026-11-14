# Engineering brief novelty

The engineering scout should interrupt only for a substantive finding, not because
a release URL or version changed. These checks are deterministic and local; they
do not require another model, embeddings, cloud inference, or a new send schedule.

## Selection and delivery

- `services/scout_novelty.py` removes download tables, bot attribution, PR numbers,
  and conventional test/docs/chore/build bookkeeping from excerpts. It retains
  user-facing feature, fix, and performance descriptions.
- Engineering mode rejects canary/nightly/abandoned/RC announcements, release
  metadata JSON, packaging-only entries, and insufficient substantive detail.
- Ranking compares content with the owner's persisted scout findings, not just
  URLs and exact titles. Version and commit identifiers do not confer novelty.
  Near-identical wording is compared only within the same publisher. Changed
  numeric claims, negation, and reordered wording are conservatively retained.
  This is a heuristic, not a semantic-understanding guarantee.
- Delivery rechecks the owner's previously attempted findings and selects at
  most one item per publisher per edition. It uses existing persisted records,
  so upgrading does not reset history or invite old briefs to repeat.
- Existing uncertain-send protections remain: an ambiguous send is not replayed
  under a different URL. No new worthwhile item means no model review or message.
- Engineering cards retain the concrete source summary and evidence label, but
  omit repeated category-level “why it matters / try this” boilerplate. Source
  links are still saved through `compact_research` and shown only when requested.

This does not broaden source authentication or change quiet hours/scouting slots.
During the November 14 audit X coverage was `not_authenticated`; available scout
sources were release-heavy. Connecting X remains a separate operator action.

## Verification

```sh
.venv/bin/python -m pytest -q tests/test_ai_scout.py tests/test_scout_novelty.py
```

Tests cover changed-URL duplicates, real new fixes and numeric changes, noisy
release metadata, invalid timestamps, owner isolation, publisher diversity,
delivery rechecks and uncertain sends. The live audit was read-only: no findings,
feedback, delivery receipts, or production history were deleted or reset.
