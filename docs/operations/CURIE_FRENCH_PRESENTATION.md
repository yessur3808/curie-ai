# Curie's French presentation

Curie's personality is presentation, not a model-intelligence upgrade. Inference
remains local. The change adds no translation model, second generation pass,
background training, or global conversation counter.

## Boundaries

- `assets/personality/curie.json` owns the voice and the opt-in
  `language_profile.french_integration.contextual_surface_fallback` flag.
- `agent/personality_context.py` uses the same conversation-history reader for
  live, compact, and regular language guidance. Casual French is encouraged;
  urgent, formal, distress, and exact-output instructions take precedence.
- `agent/french_presentation.py` recognizes meaningful expressions in the last
  three assistant replies. It discourages adjacent French touches and repetition
  of the same expression, without suppressing all French for three turns.
- The final response policy can replace an existing standalone social opening
  with its approved French equivalent (e.g. `Anytime.` → `Avec plaisir.`).
  It never prepends random French, invents a fact, or rewrites the following
  answer. Protected output, technical/command/correction turns, sensitive
  contexts, drafting/translation, and explicit language constraints bypass this
  fallback. Other personas do not inherit it.
- Recognized standalone French openings can be rendered in English when the
  model disregards an English-only request or repeats a recent touch. This is
  not a general-purpose translator or a guarantee about arbitrary model prose.
  Existing canned-suffix cleanup remains active.

## Validation

Run the offline suite with `.venv/bin/python -m pytest -q`. The focused tests are
`tests/test_french_presentation.py`, `tests/test_french_turn_guidance.py`, and
`tests/test_shared_presentation.py`. They cover history encodings, isolation,
language/format constraints, facts, repetition, and no additional model call.

Opt-in synthetic sampling of the running local inference worker:

```sh
.venv/bin/python -m evaluation.presentation_probe --suite french \
  --report evaluation/reports/presentation-french-review.json
```

This samples six conversational turns and five independent constraint cases.
It calls the model directly plus the presentation policy, not Telegram, tools,
private memory, or the production social fast path. Reports retain raw and final
synthetic output for human review. Passing unit tests does not prove every real
reply will have natural personality. Inspect for redundant greetings, misplaced
French, flattery, and repeated expressions before further tuning.

Restart the relevant application process to load Python and cached persona
changes; no inference-worker restart or host reboot is required. To disable only
the deterministic insertion/normalization, set `contextual_surface_fallback` to
false and reload the application. Keep the model's language guidance separate.
