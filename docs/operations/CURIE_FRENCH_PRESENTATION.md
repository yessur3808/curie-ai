# Curie's French presentation

Curie's personality is presentation, not a model-intelligence upgrade. Inference
remains local. The change adds no translation model, second generation pass,
background training, or global conversation counter.

## Conversation boundaries

`agent/conversation_contract.py` shares closed acknowledgement detection between
the social responder, planner, and personality adapter. Phrases such as “Thanks,
that helped” use the existing local social fast path; extra requests and negative
feedback are not swallowed. The shared response policy removes idle trailing
questions only for narrowly recognized casual updates, and caps unrequested
codas for short everyday “pick one” choices. Explicitly invited conversation,
clarification, safety caveats, requested reasons, and protected payloads remain.
These boundaries add no inference pass. They are not a general semantic fact
checker. Grounding guidance also distinguishes partial progress from completion
and previous assistant guesses from evidence; it does not guarantee factuality.
The existing calculator fast path also handles explicit outcome fractions (for
example, 18 of 20 trials succeeded → 90%), without generating the arithmetic.
It requires matching reported/requested outcomes and rejects impossible totals
or compound requests; rounded percentages are explicitly approximate.

`tests/test_conversation_contract.py` covers these contracts. For opt-in local
behavioral checks with synthetic history, run:

```sh
.venv/bin/python -m evaluation.presentation_probe --suite grounding \
  --production-style --report evaluation/reports/grounding-review.json
```

Review both raw and final replies: unknown completion must stay unknown, explicit
completion should be acknowledged, previous assistant guesses are not evidence,
and requested questions should still work. These probes neither send Telegram
messages nor write synthetic conversations to personal memory.

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

For a closer generation-path check, add `--production-style`. It uses the actual
production prompt builder, calculator, and deterministic social responder, with live recall,
awareness, and time-network lookups stubbed out. It still does not execute tools,
write conversation memory, or send Telegram messages. Reports identify the
response source and include synthetic questions as well as raw/final replies.

The November 14 conversation tests caught a misplaced greeting consuming the
short-answer budget and hiding the practical advice. Known French openings are
now constrained before sentence trimming. They also caught a model copying an
unrelated stock joke from style examples; those examples were removed.

Restart the relevant application process to load Python and cached persona
changes; no inference-worker restart or host reboot is required. To disable only
the deterministic insertion/normalization, set `contextual_surface_fallback` to
false and reload the application. Keep the model's language guidance separate.
