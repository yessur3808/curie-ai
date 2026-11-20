# Curie conversation validation — 20 November 2026

This work reuses the existing synthetic conversation suite, including the
November 16 failures. It does not treat a fresh passing sample as erasing an
earlier failure. Reports are local artifacts under `evaluation/reports/`.
All inference is local; tools are sandboxed and identities/storage are isolated.
These runs do not send Telegram messages or train on owner conversations.

## Retained repairs

- Generation and conversational history retain the validated original input.
  Routing keeps its normalized form. Previously preprocessing replaced an em
  dash before an exact-copy request reached the model; it also removed code
  indentation. Caller-supplied `_original_text` is not trusted.
- Incidental use of the noun “reason” no longer selects the reasoning role.
  Explicit “reason through/about” requests still do.
- A city correction after a closed umbrella/coat clarification can recover the
  original weather lookup and its date. Explicit topic shifts and unrelated or
  compound requests still stop the lookup-back. The baseline invented an Osaka
  forecast without executing the weather tool; the repaired replay executes it.
- Action verification handles curly apostrophes and a short contradictory
  denial in a device-action explanation. When necessary, its fallback explains
  the action using the recorded request and verified receipt, without another
  model call or tool execution.

## Evaluation records

- `curie-thorough-round1-2026-11-20.json`: full pre-change baseline.
- `curie-thorough-round2-2026-11-20.json`: routing repair and unsuccessful style
  wording experiment; the new recall guidance was missing from the compact path.
- `curie-thorough-round3-2026-11-20.json`: full suite plus fresh recall/language
  transfer cases; exposed exact-input corruption and intermittent arithmetic.
- `curie-thorough-round4-2026-11-20.json`: source-text repair and experimental
  user-statement recall reference. Exact output improved; proposal recall still
  contradicted itself.
- `curie-thorough-round5-2026-11-20.json`: retained repairs, with ineffective
  recall/style experiments removed. The first action-denial fallback was truthful
  but omitted the requested explanation; that fallback was subsequently improved.
- `curie-thorough-round6-2026-11-20.json`: repeated weather/device flows with the
  improved fallback and the held-out picnic/arithmetic/name-memory conversation.

Use `python -m evaluation.review_conversation_reports <reports...>` to apply
the same current checks to every saved report. This does not rewrite evidence.
The check for negative-status recall accepts contractions such as “aren't”; the
first transfer sample was correct and exposed a checker false positive.

## Remaining limitations

The conversation-quality goal is not complete. Observed problems include
unearned praise, invented motives/benefits, redundant questions, contradictory
proposal recall, and an intermittent unsolicited arithmetic error. A phrase-based
gate cannot catch all of these. Read the actual replies, not just the checks.

The style wording change, extra recall instructions and duplicated user-statement
reference were rejected after failing to produce reliable improvement. None is
retained. There is no new personality retry, critic pass, cloud model, training,
or underlying intelligence upgrade in this change.

Further work should evaluate local-model factual and conversational adherence
against these preserved failures and new transfer cases. Do not hide failures
with weaker assertions, broad sentence deletion, or a claim that offline tests
prove natural conversation.

## Completed verification

All six reports completed: 260 local chat turns (82, 26, 88, 24, 24 and 16).
The final targeted 16-turn report passes its objective checks. The wider reports
retain failures, including proposal recall and stock praise in round 5; there is
no claim that the complete conversation suite is passing. The source-text fix
passes the exact-output case in both rounds 4 and 5, and the repaired weather
lookup passes in rounds 5 and 6. Round 6 verifies the improved action explanation.

Final offline suite: 3,264 passed, 2 skipped, 2 deselected, 7 subtests passed;
13 deprecation warnings, 77.53 seconds. Changed Python files pass Black and
flake8, and `git diff --check` is clean. Offline tests are not an independent
semantic judge or a Telegram end-to-end delivery test.
