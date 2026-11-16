# Curie's French presentation

Curie's personality is presentation, not a model-intelligence upgrade. Inference
remains local. The change adds no translation model, second generation pass,
background training, or global conversation counter.

## Conversation boundaries

`agent/conversation_contract.py` shares closed acknowledgement detection between
the social responder, planner, and personality adapter. Phrases such as “Thanks,
that helped” use the existing local social fast path; extra requests and negative
feedback are not swallowed. The shared response policy removes narrowly recognized
idle trailing questions, without capping the number of words or sentences.
Explicitly invited conversation,
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
  live, compact, and regular language guidance. French is offered for meaningful
  speech acts such as greetings, thanks and discoveries, not every casual turn;
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
  model disregards an English-only request, repeats a recent touch, or stacks
  multiple expressions. Farewells count as touches too. Explicit French requests
  remain exempt from the touch limit. This is
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
constrained without sentence trimming. They also caught a model copying an
unrelated stock joke from style examples; those examples were removed.

The November 16 retest restored the shared neutral-preference/naturalness rules
to the compact prompt path, removed duplicate pressure to add French, and reduced
the theatrical wit checklist. This is a presentation-policy repair, not training
or an underlying intelligence upgrade. A conditional second-generation experiment
was rejected: it added latency while replacing some compliments with other
compliments. It is not present in production code.

Fresh held-out conversations also exposed two non-personality bugs: an incidental
“next to the bus stop” mention routed to live arrivals, and a continuation of a
new subject could inherit an old failed tool result. Arrival routing now requires
a request or supported shorthand. Failure evidence is scoped against the newer
active topic, while explicit questions about the old error can still retrieve it.

Run multiple end-to-end local workflow probes with repeated `--flow` options:

```sh
.venv/bin/python -m evaluation.contextual_assistant_probe \
  --flow voice_holdout --flow voice_transfer --flow error_recovery \
  --flow screenshot_replay --keep-going \
  --report evaluation/reports/conversation-review.json
```

These use the configured Curie persona, temporary identities and stores, local
inference, and sandboxed tool results. They do not send Telegram messages. The
checks are a regression floor, not a human-quality certificate: manual review
must still look for flattery, invented motives, incorrect recall and repetitive
questions. A passing report must not be described as proving natural conversation.

November 16 outcome: 3,215 offline tests passed. The final 14-turn live report
`evaluation/reports/curie-final-acceptance-2026-11-16.json` confirmed the routing,
failure-context and screenshot regressions, but failed one stock-praise check.
The separate 14-turn `curie-voice-final-repeat-2026-11-16.json` failed two such
checks. Manual review also found invented motives and ambiguous recall. These
reports are local evaluation artifacts, not proof of a production Telegram
delivery or high conversational quality. The remaining voice problem is open;
do not hide it by weakening the checks or adding destructive sentence filters.

Restart the relevant application process to load Python and cached persona
changes; no inference-worker restart or host reboot is required. To disable only
the deterministic insertion/normalization, set `contextual_surface_fallback` to
false and reload the application. Keep the model's language guidance separate.
