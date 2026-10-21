# Conversation templates

This directory is the canonical home for reusable live conversation tests. The
templates are source-controlled evaluation fixtures. They are **not** inserted
into Curie's personal, working, or long-term memory.

Each run creates a unique synthetic owner, keeps one native session for
multi-turn continuity, and closes that session in a `finally` block. A run is
reported as failed if Curie does not provide a native session ID or the cleanup
cannot be confirmed.

## Use

```bash
python -m evaluation.conversation_template_runner --list
python -m evaluation.conversation_template_runner natural_conversation
python -m evaluation.conversation_template_runner operator_assistant
python -m evaluation.conversation_template_runner --all
```

Use `--no-voice` for a faster text-only diagnostic. That override will cause a
template which explicitly checks trained voice to fail, as intended. Reports
are not persisted by default. To keep one deliberately:

```bash
python -m evaluation.conversation_template_runner natural_conversation \
  --report evaluation/reports/natural-conversation.json
```

Generated JSON reports under `evaluation/reports/` are ignored by Git.

## Add a future template

1. Copy one of the stored JSON files and give it a unique lowercase `id`.
2. Preserve the user's exact test wording in `turns`.
3. Add deterministic checks for the behavior the exchange is intended to test.
4. Run `pytest -q tests/test_conversation_template_runner.py`.
5. List the catalog, then run the new template against a healthy local Curie API.

The authoritative structure is `schema.json`. Supported checks are deliberately
small and deterministic: completeness, API errors, forbidden wording, response
similarity, per-turn required or forbidden phrases, word and line limits, bullet
counts, sentence-mark limits, and trained-voice generation. Add a runner check and unit
coverage before using a new check type.

Never add credentials, real private conversations, API tokens, or personally
identifying data to a template. Sanitize production-derived regressions first.
