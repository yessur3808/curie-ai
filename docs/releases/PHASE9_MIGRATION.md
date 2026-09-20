# Phase 9 migration

Phase 9 changes evaluation and release tooling only. It adds no database schema,
connector credential, network, model, or runtime-service migration.

Run the complete offline gate with:

```bash
python -m evaluation.release
```

The generated JSON report is written to `evaluation/reports/latest.json` and is
ignored by Git. CI may use `--skip-tests` only after the full offline pytest job
has already passed in the same revision.

Rollback by restoring the prior reviewed application commit. Generated reports
can be deleted safely; no user or memory data is changed by the evaluator.
