# Curie evaluation system

Phase 9 makes evaluation a release contract rather than a collection of prompt
examples. The default path is fully offline and deterministic:

```bash
python -m evaluation.release
```

This command runs the offline tests, the phase suites, provider simulations,
catalog validation, latency sampling, and every blocking gate. It writes a
privacy-safe report to `evaluation/reports/latest.json` and exits non-zero when
the release is blocked.

## Layout

- `taxonomy/` defines stages, behavioral leaves, and critical intents.
- `datasets/` contains versioned portable cases and sanitized production
  regressions. Every case names its owner/provider fixtures, expected route,
  entities, plan constraints, required and forbidden tools, verification,
  response facts, forbidden claims, style, tags, and risk.
- `graders/` contains deterministic graders and the human-calibration boundary
  for subjective model graders.
- `runners/` contains restricted execution policies, including fail-closed
  credentialed canaries.
- `release_gates.json` owns thresholds, calculations, datasets, blocking
  behavior, components, and waiver rules.
- `reports/` contains generated reports; JSON output is intentionally ignored.

## Evaluation layers

The release report combines deterministic units, understanding and routing,
plan shape, provider/tool simulation, memory behavior, conversation and
personality, connector delivery, and stable production regressions. Optional
model judgments may supplement naturalness and warmth only after calibration
against human labels. They cannot approve correctness, security, safety, tool
selection, or release readiness.

Model comparisons must hold prompts, context, schemas, decoding, hardware,
timeouts, datasets, and case ordering constant. Credentialed canaries are
disabled by default, require named harmless sandbox resources and cleanup, and
reject finance, trading, public posting, and outbound email capabilities.
