# Curie Release Process

`README.md` remains the canonical setup and operations guide.
`ENHANCEMENT_ROADMAP.md` remains the canonical capability roadmap. This page
defines only the release procedure.

Every significant release updates `release/current.json`, `CHANGELOG.md`, an
evaluation delta under `evaluation/deltas/`, migration notes, and executable
rollback instructions. CI validates those references and the public contract
version before tests can pass.

Before release, create and verify a private memory backup, apply pending schema
migrations, run the complete offline/security/conformance suites, and record the
evaluation delta. Deploy gradually, inspect `/health` and `/capabilities`, then
retain the prior reviewed revision until the observation window closes.

Rollback application code first. Restore data only when the schema/data itself
is invalid, because restoring discards newer state. SQLite rollback uses the
versioned migration API and the pre-restore copy created by the recovery tool.
PostgreSQL rollback requires an explicit target:

```bash
python -m scripts.down_migrations --target VERSION
```

Never edit an already-applied migration. Add a new numbered up/down pair.
