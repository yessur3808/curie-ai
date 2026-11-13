# Curie trained voice

Current selection (2026-11-10): **Nano B**, revision `curie-french60-b-360steps`, restored after the owner rejected the multilingual emotional samples for degraded quality and robotic echo. Experimental output prosody is disabled; input delivery cues remain enabled.

## Service ownership and startup

The canonical implementation is `services/trained_voice/` in this repository. This includes the conversation-only HTTP API, local speech and transcription workers, persona delivery, streaming/cancellation helpers, dataset preparation, LoRA training and evaluation. The Observatory dashboard is a client. No dashboard checkout is required to run this API after provisioning Curie's models and runtimes.

From the Curie checkout, start `.venv/bin/python -m services.trained_voice.api`, or use `pm2 startOrReload deploy/ecosystem.trained-voice.config.cjs --only curie-dashboard-api --update-env`. The service binds only to `127.0.0.1:8010`; the dashboard retains authenticated HTTPS access. The existing process name is retained for operational compatibility. Starting this service does not start Curie's other bot channels. For the one-time migration from the old dashboard script, first run `pm2 delete curie-dashboard-api`, then `pm2 start deploy/ecosystem.trained-voice.config.cjs --only curie-dashboard-api` and `pm2 save`: PM2 reloads can retain the old script path when changing executables.

`CURIE_ROOT` optionally selects the Curie checkout. `CURIE_VOICE_HOME`, `CURIE_VOICE_PYTHON`, and `CURIE_TRANSCRIBE_PYTHON` can override the default data directory and interpreter paths shown below. Use absolute paths for overrides. Standard-library-only `VoicePaths.ready()` checks model metadata without importing Torch. Synthesis and recognition use bounded isolated warm workers with thread limits, idle eviction, and cancellation. One-shot synthesis remains available as a rollback.

Endpoints are `GET /health`, `POST /chat`, `POST /speak`, `POST /speak-stream`, `POST /transcribe`, and `GET /audio/{name}`. `/speak-stream` accepts `{"text":"Bonjour. I am here.","voice_profile":"trained"}` and returns newline-delimited `ready`, `audio`, and `done`/`error` events. `trained` is the default profile; the older profile names remain accepted for client compatibility but do not override a ready trained voice. Audio URLs are private to the same service. Keep this API on loopback behind the dashboard; it is not an independently authenticated public server.

The migration moved the existing private data and both isolated environments into Curie without retraining or changing the adapter/configuration. Old dashboard data/runtime paths are compatibility symlinks to these directories, not duplicate model copies. Keep the symlinks until virtual environments are recreated at the new location and archived absolute evaluation paths are updated. Do not install Torch or the trained speech engine into Curie's general `.venv`; `.trained-voice-venv/` remains isolated. The pinned speech runtime package list is preserved privately in `data/trained-voice/runtime-freeze.txt`.

Run the lightweight regression tests with `python -m unittest discover -s tests -p 'test_trained_voice_*.py'`. Run the same command using `.trained-voice-venv/bin/python` to include the adapter tests. The existing repository CI discovers these tests automatically.

## Trained model

Curie AI owns **Chatterbox Nano with an actual locally fine-tuned LoRA adapter**, a saved Curie speaker profile, and Curie's active personality. The earlier version used one reference clip without weight training. New speech works without Internet access once the server is provisioned; desktop companions call the private server. Listen at `/voice.html` to compare matching previous and updated greetings. Output is synthesized and resemblance is not guaranteed.

## September 2026 training run

We fetched all **500 distinct original Curie WAVs** (30.6 minutes) in the public collection below. Enhanced/equalized copies were excluded. Duration and transcription-confidence filtering retained 355 Curie clips: **325 for training and 30 held out for validation**. Six English segments from **two adult native French women** supplied supplementary accent data. A third public French speaker recording was examined but did not survive filtering. This is a documented collection, not a claim to have exhausted every recording on the Internet.

The 361 accepted clips contain approximately 26.9 minutes of speech. Transcripts were generated locally with faster-whisper base, English, beam size 3, confidence filtering and word timestamps. They are not manually verified ground truth. Conditioning uses a separate training clip from the same speaker; validation clips are never used as conditioning references.

Training updates **1,179,648 LoRA parameters**, rank 8, alpha 16, on the Nano GPT-2 attention and MLP projections. All other weights remain frozen. The run used AdamW, learning rate 0.0001, gradient accumulation 2, 240 optimizer steps, seed 852, and 85% Curie / 15% supplementary French sampling. It completed in **305.2 seconds** after feature preparation, under a five-core CPU quota and 12 GiB memory limit. Teacher-forced held-out loss fell from **4.8310 to 4.0893**. This measures token prediction, not perceived French accent quality.

**Full-strength inference repeated or dropped words despite the lower validation loss.** The deployed adapter is therefore blended at **0.20**, with sentence-sized chunks capped at 180 characters. Tests compared the old profile, the new reference alone, and strengths 1.0, 0.65, 0.35 and 0.20. The conservative version retained the greeting and short factual test sentences much better. Longer speech was checked separately. Names, numbers and accent authenticity still need human listening; ASR cannot prove that a voice sounds like Curie.

The production adapter SHA-256 is `8975273590704566ecf22897b1a35488f0fd1dd458b7ee8d5abb8489f7302d0a`. Full numerical training results are in [voice-training-v2.json](voice-training-v2.json). Private dataset manifests, source hashes, rejected examples, evaluation recordings and intermediate checkpoints remain on the server for auditing and rollback.

## Sources and usage terms

- Engine: [Resemble AI Chatterbox](https://github.com/resemble-ai/chatterbox), revision `5de7a54aa4e5e2baadb0182dde554908b48b85c2`.
- Base model: [Chatterbox Nano](https://huggingface.co/ResembleAI/chatterbox-nano), revision `71ccd1d0081b430592cea481f4307e764e07bc64`, MIT.
- Curie recordings: [Rootreck's Fallout 4 dialogue collection](https://huggingface.co/datasets/Rootreck/Fallout_4), original `dataset/44k/F4_F_NPCFCurie_Eng/` WAVs. The manifest pins the dataset revision and each clip's SHA-256. The uploader attributes the performance to Sophie Cortina but declares no dataset license. Public availability does not transfer the original game/performance rights or imply endorsement.
- Supplementary accented English: George Mason University's **Speech Accent Archive**, [French 14](https://accent.gmu.edu/samples/french14/) and [French 59](https://accent.gmu.edu/samples/french59/), adult native French speakers from Paris, **CC BY-NC-SA 4.0**. Source URLs, speaker attribution and recording hashes are stored in the private manifest. Retain attribution, noncommercial and share-alike obligations when considering reuse; this voice is not packaged for commercial redistribution.
- The independent [Nano training-contract investigation](https://github.com/Pseud0naut/chatterbox-nano-language-training) informed speech-only shifted causal training. The scripts in this repository implement that contract without loading third-party training checkpoints.

Original recordings, conditioning tensors and model/adapter weights are **excluded from Git and desktop installers**. Only short synthesized comparison greetings are included. Chatterbox's built-in watermark is retained.

## Personality and delivery

The bridge loads the same `PERSONA_FILE` as the existing Curie service (`curie.json` on this server), including the complete system prompt, response style, language profile and style modulation. It uses Curie's native contextual personality directives for conversation. Briefing formatting is appended to that profile instead of replacing it with a generic prompt. Normal English spelling is retained; artificial phonetic spellings and decorative French must not alter factual values.

Professional, casual, emotional and urgent contexts set bounded pauses and speaking rate; the worker also passes the temperature setting to Nano. Nano does not offer functional CFG/exaggeration controls, so those are not presented as personality effects. The personality file is reloaded when changed. Voice and personality revision IDs invalidate saved briefing audio automatically, preventing the old voice from being replayed after an update. The bridge has no action tools or outbound messaging connectors.

## Private server layout

All paths below are relative to the `curie-ai` repository. The HTTP API and all speech/training code live in `services/trained_voice/`. The dashboard only calls this service and retains a compatibility launcher.

```
.trained-voice-venv/                       isolated CPU speech runtime
.transcription-venv/                             offline transcription runtime
data/trained-voice/config.json                  active configuration
data/trained-voice/config-v1.json               previous configuration for rollback
data/trained-voice/chatterbox-nano/              official base weights and tokenizer
data/trained-voice/training-v2/curie/            original clips and provenance
data/trained-voice/training-v2/french-english/   attributed supplementary recordings
data/trained-voice/training-v2/dataset.json      filtered transcripts and split
data/trained-voice/training-v2/features.json     independent conditioning manifest
data/trained-voice/training-v2/run/              metrics and safetensors adapters
data/trained-voice/training-v2/curie-profile-v2.pt
```

The source archive's third-party checkpoints are never downloaded or executed. Official model and locally trained adapter weights use safetensors. Locally generated feature and conditioning tensors use `weights_only=True` on load. The isolated runtime freeze is saved at `data/trained-voice/runtime-freeze.txt`.

Configuration paths are relative to `config.json`:

```json
{
  "enabled": true,
  "engine": "chatterbox-nano",
  "model": "chatterbox-nano",
  "profile": "training-v2/curie-profile-v2.pt",
  "adapter": "training-v2/run/best.safetensors",
  "adapterStrength": 0.2,
  "threads": 6,
  "method": "lora-fine-tuning",
  "revision": "curie-v2-lora240-strength20"
}
```

Training helpers are separate from normal app startup. With the private source manifests provisioned, reproduce feature preparation and training using:

```sh
# Set FFMPEG_BIN to the local ffmpeg executable if not on PATH.
.transcription-venv/bin/python -m services.trained_voice.prepare_voice_training transcribe --root data/trained-voice/training-v2
.trained-voice-venv/bin/python -m services.trained_voice.prepare_voice_training features \
  --root data/trained-voice/training-v2 --model data/trained-voice/chatterbox-nano
.trained-voice-venv/bin/python -m services.trained_voice.train_voice_adapter \
  --root data/trained-voice/training-v2 --model data/trained-voice/chatterbox-nano --steps 240 --threads 6
```

Run training under an explicit CPU/memory quota and keep candidate adapters separate from the production config until listening and generation checks pass. `evaluate_voice_adapter.py` generates comparisons; `score_voice_samples.py` produces diagnostic ASR results. `prepare_reference_voice.py` builds conditioning tensors from the selected reference; it is not weight training.

## Runtime and rollback

Health checks read metadata and never import the speech model. A shared cross-process file lease prevents the dashboard and bot connectors from loading multiple 4–6 GiB workers at the same time. A request waits for a short bounded queue interval, then fails as busy; it never creates an overlapping model process. The worker uses six CPU threads at reduced priority, offline model access, bounded text and incremental audio output, then exits. The bridge kills it after 115 seconds. Report line breaks are preserved as sentence boundaries, and visual separators become spoken pauses. Invalid or unusually long chunks get one bounded retry with the same text and trained voice; persistent failures are rejected rather than serving likely repetition. A failed generation leaves text available and never silently switches voices.

The updated 10-second greeting took approximately 12 seconds including model loading, with **4,434 MiB peak worker RSS** in the isolated test (live chat workers have reported up to about 5.6 GiB) on the Ryzen AI 9 HX 370 server. A 213-word report completed in 80.44 seconds across 21 chunks with no retries, producing about 105 seconds including pauses. The live dashboard request then completed in 78 seconds; cached replay took 10 ms over the tested Meshnet connection. Speech generation is a temporary memory-heavy operation; the lightweight Go dashboard does not load the model and there is no resident cloning worker. Cached replay avoids new inference. Briefing audio is optional and never starts automatically.

The trained worker is now Curie's authoritative outbound voice across Telegram, Discord, Slack, WhatsApp, the main API, and this dashboard bridge. `CURIE_TRAINED_VOICE_REQUIRED=true` is the default. When the trained voice is missing, busy, times out, or fails validation, callers keep the text response and omit audio rather than falling back to another identity.

To roll back the adapter, atomically restore `data/trained-voice/config-v1.json` as `config.json`, then restart only PM2 process `curie-dashboard-api`. The old profile and original base weights remain intact. For an emergency engine rollback, explicitly set `CURIE_TRAINED_VOICE_REQUIRED=false`; only then may the legacy Piper/eSpeak or consent-gated XTTS paths run. This switch should be temporary and visible in health output.

## Live conversation and v2.1 delivery

The dashboard now supports automatic spoken turn-taking and streams completed WAV sentences from the same one-shot trained worker. The current refinement keeps the adapter and reference unchanged, reduces sampling temperature by 0.07 and sentence pauses by 10%, and retains Curie's contextual personality settings. These are inference refinements, not additional weight training. Current and previous trained greetings are available at `/voice.html`. See [dashboard live voice notes](https://github.com/yessur3808/curie-observatory/blob/main/docs/LIVE_VOICE.md) and [diagnostic results](voice-refinement-v3.json).


## Conversation evaluation (November 2026)

Use `services/trained_voice/conversation_eval_prompts.json` with the evaluator's
`--prompts` argument for 14 fixed passages covering greetings, questions,
corrections, uncertainty, bus routes/numbers, reassurance, longer turns, and
English/French switching. These are evaluation passages, not training material.
The optional JSON records preserve category and language in `samples.json`;
`language: "auto"` enables ASR language detection for mixed passages. Automatic
recognition may mistranscribe code-switching, so those passages require listening.

Score PCM16 samples with:

```sh
.transcription-venv/bin/python -m services.trained_voice.score_voice_samples \
  --directory /path/to/private/evaluation --output scores-v2.json
```

Metric version 2 keeps French accents and apostrophes, preserves all numerical
values, and normalizes English numbers below sixty so `18X` and `eighteen X` do
not count as different words. It also reports measured WAV duration, peak level,
clipped sample fraction, quiet sample fraction and words per minute. Quietness
uses 20 ms energy windows below 0.005 RMS; it is not a linguistic pause detector.
These diagnostics do not prove naturalness or speaker identity. Compare matching
prompts and settings, preserve old reports, and listen to the recordings.

The November 10 existing 18 samples were rescored without new synthesis or
training. All three variants scored 1/105 word errors (0.95%); the remaining
transcription difference was Macao versus Macau/Mako. The lower reported error
rate is due to corrected number normalization, not a new voice improvement.
The report is privately stored at
`data/trained-voice/naturalness-20261110/scores-v2.json`.

Further original-voice training requires a consenting speaker and documented
permission for voice-model training. Do not extend the old game-character
imitation dataset. Record conversational English and brief natural French
expressions, verify transcripts manually, and keep recording sessions separated
between training and evaluation. Compare omissions, repetitions, numbers,
pronunciation, audible artifacts, conversational pacing and voice consistency.
A lower training loss or WER alone is not grounds to activate a candidate.
No new checkpoint was trained or promoted in this update.


## Live recognition and prompt latency update (November 10, 2026)

The dashboard `/transcribe` endpoint now opts into an isolated resident
faster-whisper base/int8 worker. It uses two CPU threads, local cached weights,
one request at a time, a three-second queue limit, the existing recognition
request timeout and a 120-second idle unload. Each API process has at most one
worker for this root. Cancellation kills the active worker; cancellation of a
queued caller does not affect the active caller. Shutdown closes workers. Worker
failure falls back to the original one-shot path. Telegram and other callers
retain their original default behavior. Neither audio nor transcripts are cached.
Set `CURIE_WARM_TRANSCRIPTION=false` to restore one-shot dashboard recognition.

The conversation-only API now supplies an honest scoped capability description
instead of the full tool index. Ordinary replies avoid the redundant generic
quality checklist; complex-work guidance, draft review, verification and response
repair remain. Live turns use concise dynamic personality guidance; text-only
turns keep their existing guidance. Roll back independently with
`CURIE_SCOPED_CONVERSATION_PROMPT=false` and `CURIE_COMPACT_LIVE_PROMPT=false`.
Restart the dashboard API processes after changing environment settings.

The client passes `previous_reply_interrupted` on the next question after playback
is interrupted. The model is told that history does not prove the whole reply
was heard. This does not yet identify the exact final word that was played.
Direct-provider stage metrics now include latency, prompt characters and output
characters; they remain completed-answer measurements, not first-token timing.

Private paired evidence is in `data/latency-20261110/`. Three ephemeral text checks
went from 10.928–11.465 seconds to 4.751–7.226 seconds (median 11.198 to 5.859).
These exclude speech generation and ordinary session directives. A separate
three-question live/session test of the compact guidance changed 9.555–12.104
seconds to 5.607–8.031 (median 10.249 to 7.301); its baseline already included the
capability-scope optimization, so these percentages must not be added together.
A repeated four-second recording transcribed identically: one-shot repeat calls
1.437/1.487 seconds, warm repeat calls .462/.460. Real HTTP warm repeat calls
.608/.562 seconds. These are small diagnostic samples, not p95 or universal
latency claims. No audio synthesis, voice-weight training or voice change was
performed. Whole-answer-before-speech and one-shot TTS remain; genuine token-to-
speech streaming and warm TTS have not been implemented in this update.

A live correction probe also exposed an existing cleanup bug: an explicitly
requested question such as "ask me which stop" was being removed. The live
finalizer now recognizes that form, with a regression test. Existing requests
for "ask me a question" keep their behavior.

### Internal draft transport (2026-11-10 follow-up)

Ordinary `conversation_only` requests with a local-only provider configuration now
use genuine llama.cpp delta events over the private inference socket. The protocol
separates `draft_delta`, authoritative cleaned `done`, and generic `error` frames.
It remains serialized in the existing worker; no duplicate model is loaded.
Drafts are **not approved speech or display text**: they may contain reasoning or
text removed by cleanup. The model service observes only timing/counts and still
runs the existing verification and live finalization before delivery. Review,
specialist, JSON and cloud routing retain their existing contracts.

`first_draft_ms` and `draft_characters` supplement completed-answer stage metrics.
They are not first-useful-audio measurements. The stream does not replay a failed
draft or run an in-worker quality retry after emission. Since nothing has reached
the user, the orchestration layer may still recover and verify a replacement.
An interrupted request closes its socket at the next delta, closes the native
iterator, and releases the model lease. Native prefill remains non-interruptible
until the backend yields; this is not a hard real-time cancellation guarantee.
Set `CURIE_LOCAL_DRAFT_STREAM=false` and restart the API to restore atomic local
requests. Worker protocol support is backwards compatible with existing clients.

The dashboard now carries interruption context even when a reply was still being
prepared, and across End/Start in the same page. A reply is considered completely
played only after all audio finishes. This is conservative delivery tracking, not
word alignment. Checked sentence-to-TTS overlap and exact played-text tracking
remain subsequent roadmap work; the public chat response still waits for the
verified complete answer. No voice weights or speaker profile changed.

Smoke evidence (`data/latency-20261110/draft-stream*.json`, private): a direct
short-prompt model probe returned its first draft at 24.446s after worker restart,
then at .397s warm, with warm completion at 3.137s. Cancellation after the first
delta allowed a serialized worker status request 54ms later. An actual API live
prompt (2,406 prompt characters) reported first draft at 4.036s and verified HTTP
completion at 6.634s, with no reasoning tag in the returned answer. This exposes
remaining prompt/prefill cost; it does not establish an audio-latency reduction.

### Natural conversation target and release feedback (2026-11-10)

The owner wants Dessa-level or better conversational naturalness while retaining
Curie's voice identity, French accent and occasional contextual French. Treat
this as an evaluation target, not an achieved capability or guaranteed result.
Block describes Dessa as native speech-to-speech and full-duplex, trained on
conversation audio (https://block.xyz/dessa, reviewed 2026-11-10). Curie's current
transcription → text model → speech pipeline does not preserve all vocal cues.
A smoother synthesizer alone cannot establish equivalent conversational behavior.

Prioritize checked sentence/audio overlap and resident speech inference, then
adaptive turn endings and interruption recovery. Evaluate phrase-level pitch,
stress, speaking rate, pauses, cross-sentence continuity, and pronunciation with
an original consenting speaker. Keep spontaneous French sparse and appropriate;
do not force filler, laughter or acknowledgements into every reply. Compare
candidates using the same held-out prompts and interactive scenarios, including
mid-sentence interruptions, hesitation, correction, route numbers and names.
Use listener preference alongside actual first-useful-audio latency; transcript
accuracy alone does not measure naturalness. Native audio understanding or a
speech-to-speech backend is a separate architecture candidate requiring testing
for voice continuity, tool grounding, hardware cost and interruption behavior.

Owner explicitly authorized Telegram voice release notifications: after each
successfully deployed Curie enhancement, send the owner a short voice message
from Curie describing what changed, with a relevant listening example when
available. Use the established owner recipient and supported original voice;
never guess a chat ID or silently change speaker identity. Report delivery only
after Telegram confirms success. If audio generation/delivery is unavailable,
report that limitation rather than claiming a message was sent. This is a
standing release-workflow instruction, not a scheduled task or an implemented
automatic deployment hook. Documentation-only updates do not establish an audio
improvement to demonstrate.

### French 60 accent candidate (2026-11-10)

After the owner selected GMU French 60 and requested training, a separate
360-step LoRA candidate was trained under `data/trained-voice/french60-20261110/`.
Production configuration, adapter and conditioning profile were hash-checked and
remain unchanged. The new source is https://accent.gmu.edu/samples/french60/;
the private source manifest records its audio URL, SHA-256, attribution to the
Speech Accent Archive / George Mason University, and CC BY-NC-SA 4.0 terms.

The clip yielded 26.93 seconds in three segments. Two (19.93s) supplement the
existing 325 primary and six French-English training clips; the third (7s) joins
the existing 30 validation clips. Conditioning targets are distinct and all
conditioning IDs come from training. ASR spelling/phrase errors were corrected
against the published elicitation passage; this is not a human audio audit.
Other archive speakers read that passage too, so this split is not text-disjoint
and cannot establish generalization to unseen language.

The run used 4 threads, a 4-core/12-GiB scope, learning rate 0.00005, and 1,179,648
trainable parameters. Best step360, held-out loss4.84326 →4.12970; elapsed434.3s,
peak4280MiB. These losses use a different validation set from the earlier run.
Three matched passages compare cached production audio with the new adapter at
.20 and .25, retaining the production speaker conditioning and delivery settings.
All three variants scored1/49 ASR word errors (Macao/Macau); candidate generation
needed no retries. This is a small intelligibility check, not proof of stronger
accent, naturalness or preserved speaker identity. Candidate configuration files,
matched preview WAVs, `scores.json`, scripts and `experiment.json` are private.
No candidate was activated; listening review and broader testing remain needed.

### B selected and contextual French restored (2026-11-10)

The owner preferred audition B and requested brief French words/phrases in speech.
B is now active at adapterStrength0.20, revision `curie-french60-b-360steps`, with
the same speaker profile. Rollback configuration is
`data/trained-voice/config-before-french60-b.json`.

The auditions used fixed English passages; accent training does not insert French
words. Live compact personality directives had also omitted the contextual French
examples. Live and regular compact prompts now share a turn-specific directive:
brief expressions for greeting/agreement/discovery/empathy, English dominant,
recent-expression suppression, and English-only/exact wording overrides. Commands,
urgent and professional turns retain clear factual wording. Curie's persona no
longer discourages French throughout a whole conversation. This changes word
selection, not another voice-weight training run; expression choice remains model
generated rather than an unconditional suffix added to every response.

A second suppression point was identified in ResponsePolicy: its leading-French
cleaner removed voilà by default even if generated appropriately. The explicit
Curie setting `preserve_contextual_expressions` now permits that contextual use
and brief asides on casual updates, while retaining recent-use, operational,
English-only and suffix guards. Real normal-session HTTP checks produced a casual
reply with Voilà and an English-only follow-up without French; the test session
was closed afterward. Ephemeral probes skip personality directives, so they do
not validate this behavior. 83 focused tests plus7 subtests passed; 8010/8011
healthy after restart. Telegram voice update confirmed, message651 (19seconds).

### B naturalness continuation and warm synthesis (2026-11-10)

The owner-selected French60 B remains the active adapter at strength 0.2. A separate
180-step continuation starts from B's exact adapter tensors with a fresh optimizer
and learning rate 0.000015. `--initial-adapter` validates tensor names, shapes,
finiteness and scale before restoring; it retains the starting checkpoint if no
validation improvement occurs. This reuses existing dialogue and French60 features,
not new human-rated prosody data. Lower token loss is not evidence of human-rated
naturalness. Candidate artifacts and matched auditions are private under
`data/trained-voice/naturalness-b-20261110/`; do not automatically promote them.

`CURIE_WARM_SYNTHESIS=true` (default) reuses the isolated speech process between
requests. Each application process keeps at most one model, evicts it after 60 idle
seconds, and reloads when configuration or model/profile/adapter metadata changes.
The cross-process synthesis lease still serializes generation. Different API/bot
processes can retain idle copies, so this is not a single shared inference daemon.
Cancellation or timeout kills the active process group; queued cancellation does
not kill another request. API shutdown closes resident speech workers. Set
`CURIE_WARM_SYNTHESIS=false` and restart the application for one-shot rollback.
The first request after eviction still pays model loading cost.

One-shot and resident workers share synthesis, seeding, delivery and validation.
Long utterances prefer clause boundaries over arbitrary word cuts; spoken reply
instructions favor direct, specific responses, contractions and varied sentence
lengths without removing requested facts. These are delivery/prompt changes,
separate from adapter training. Streaming still starts after the language model's
complete checked response; this does not implement model-to-speech overlap.

Verification: 151 focused Python tests plus 7 subtests passed; all 3 adapter tests
passed in the isolated Torch environment. Both deployed APIs returned HTTP 200
and retained revision `curie-french60-b-360steps`. Live reply probes exercised
practical help, emotional context and an explicit English-only explanation;
prompt adherence remains imperfect, and no model response-quality guarantee is
implied.

The continuation completed in 234.3 seconds: held-out token loss 4.129703 to
4.114416. Initial matched auditions exposed a poor short French-aside boundary.
Speech preparation now joins an opening `D'accord. Let's ...` into
`D'accord, let's ...`, also handles a small set of equivalent French asides, and
normalizes curly apostrophes. Words are preserved; French-only passages are not
joined by this rule. In the three-phrase test, both B and the candidate improved
from 13/53 ASR word errors to 4/53 after this preparation fix. ASR still misheard
the French phrase and some English/name wording. The candidate is not promoted.

Initial repeated-phrase benchmark before the aside fix: one-shot median 15.61s
versus resident repeat median 9.37s, identical WAV hashes. After the fix, repeated
resident generation took 4.41s and 4.59s for a 5.6s utterance; its first streamed
chunk arrived at 2.55s. This excludes language-model response time and physical
playback latency. Cold final samples overlapped live reply probes and are not a
controlled comparison. These are small local diagnostics, not end-to-end p95
measurements. Details are in the private `latency*.json`, `scores*.json`, and
`replies-*.json` artifacts.

### Multilingual pronunciation and voice cues (2026-11-10)

The owner reported incorrect `d’accord`/`voilà` pronunciation and flat delivery,
and chose French accuracy and emotion over preserving B exactly. The trial (subsequently rolled back)
`chatterbox-multilingual` engine uses official `ResembleAI/chatterbox` revision
`5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18`, `t3_mtl23ls_v2.safetensors`, `s3gen.pt`,
`ve.pt`, and the multilingual tokenizer. The same private Curie reference audio
conditions the new model; B's Nano adapter is incompatible and is not merged.
The model supports real language tokens and emotion conditioning, unlike Nano's
ignored CFG/exaggeration arguments. Model documentation:
https://huggingface.co/ResembleAI/chatterbox-nano (family comparison and multilingual usage).

`language_spans.py` separates known French phrases from surrounding English,
canonicalizes unaccented `voila`, and uses local English/French Lingua confidence
plus conservative word rules for longer French passages. It does not turn an
entire English sentence French because it contains `café`. Ambiguous homographs
still require context. Production uses `lingua-language-detector==2.2.0` in the
isolated speech environment; there are no network requests during detection.
Exploratory phonetic English spellings were not shipped. Text replies retain
normal spelling. New readiness/one-shot plans use Python checks for this engine
because the installed Rust extension recognizes only Nano; native leases and
other runtime coordination remain active.

`prosody.py` computes bounded local pitch variation, relative voiced activity,
level and clipping diagnostics. It rejects silence, short/unvoiced/noisy input,
nonfinite samples and heavy clipping. Only low-confidence `quiet`, `steady` or
`animated` delivery cues leave the recognition worker. These are **not validated
emotion labels**: a quiet mic is not proof of sadness, and animated delivery can
be distressed or excited. User words take priority. `affect.py` selects gentle,
warm, bright or clear response delivery using wording and these weak cues;
urgent/professional contexts remain clear. Multilingual exaggeration values are
0.6/0.65/0.8/0.4 respectively, CFG0.3, with bounded pace/pause changes.

Both Telegram and live voice carry cues into personality guidance and outgoing
speech. Telegram scopes them with a ContextVar around one workflow invocation;
text-only calls have no cues. Live API associates transcript/reply hashes with
owner ID, single consumption, 120-second TTL and capacity64. The dashboard speech
proxy now forwards the configured owner ID, not a caller-supplied owner. Audio
still uses the existing temporary-file cleanup; no emotional profile is persisted.
`CURIE_VOICE_AFFECT=false` plus restart disables acoustic analysis and affect-based
selection. This does not disable the multilingual language model itself.

Active config is `data/trained-voice/config.json`; rollback config is
`data/trained-voice/pronunciation-emotion-20261110/config-before-multilingual.json`.
To revert the voice, atomically restore that JSON and restart curie-main and both
API processes. Multilingual profile, source manifest, runtime freeze, probes and
samples are in that same private experiment directory. The dashboard binary
backup is `dashboard/data/deploy-backups/dashboard.before-voice-affect-20261110`.

Initial CPU quality evaluation: French four-phrase clip and both English emotion
samples had zero ASR word errors; mixed French/English had only the unaccented
`Voila` spelling difference. ASR does not establish authentic pronunciation or
perceived emotion. Native decoder samples took 20–37s to synthesize; an actual
API cold mixed-language stream produced first audio at14.76s and finished24.11s.
This is a substantial latency tradeoff, not a ChatGPT-like realtime result.
Warm reuse/cancellation remain enabled. A gentle synthetic clip was classified
as acoustically animated, illustrating why cues must not be called emotion labels.

Verification:158 Python tests plus7subtests, two isolated acoustic signal tests,
Go tests and dashboard build passed; both APIs and the dashboard service were
healthy after deployment. Real `/transcribe` -> `/chat` -> `/speak-stream` smoke
check completed using a synthetic sample; physical microphone/listening review
remains with the owner. No weight training was performed for this engine switch.


### Quality rollback after owner listening (2026-11-10)

The owner reported that the final two Telegram samples (659 bright, 660 gentle)
had severely deteriorated quality and a strange robotic echo. ASR accuracy did
not validate perceived voice quality. Restored the original B config and removed
the experimental Nano per-chunk rate variation. `CURIE_EXPRESSIVE_OUTPUT` now
defaults to false, preserving prior delivery settings independently of input cues.
No echo effect is deliberately added by the Telegram encoder. The exact artifact
cause remains unverified; synthesis, reference conditioning and time stretching
need isolated listening comparisons before another promotion. French accuracy and
emotional output remain unresolved on B. The private decoder-swap timing probe
is experimental and was not deployed.

### Paired auditions and latency stages (2026-11-11)

`python -m services.trained_voice.compare_voices --baseline CONFIG --candidate CONFIG
--prompts services/trained_voice/pronunciation_eval_prompts.json --output NEW_DIRECTORY`
creates private PCM16 auditions using the same prompts, synthesis path and seed.
The candidate is optional for a baseline run. No active config is written.
Reports bind config/profile/adapter hashes to the actual generated waveform hashes.
`listening-review.json` starts with null checks for identity, pronunciation,
naturalness, no echo and no distortion. Fill these only after listening. Run the
same command with `--output DIRECTORY --check-review` to check reviews; altered
audio or incomplete/rejected listening fails. This records a review result, not
automatic deployment authorization. Optional ASR scoring uses the existing
`score_voice_samples` command and remains diagnostic only.

The fixed pronunciation set includes d’accord, voilà, bien sûr, je comprends,
English homograph controls, French-only speech and the bright/gentle phrases
involved in the rejected engine trial. Preserve baseline artifacts when comparing
changes. The existing B pronunciation limitations are not fixed by adding tests.

Worker metrics now separate model preparation, first chunk and synthesis time,
and distinguish cold from reused models. Live API `/transcribe` returns
`timings.transcription_ms`; `/chat` returns `reply_ready_ms` (queue/context/reasoning
and response preparation) and `speech_ms`; `/speak-stream` ends with
`first_audio_ms` and `synthesis_total_ms`. These are server-side measurements,
not microphone endpointing, network transfer or device playback delay.

Andreja preparation and candidate artifacts are isolated in
`data/trained-voices/andreja/experiment-20261111`. The generic LoRA trainer accepts
`--target-source andreja --supplement-source none`, so Curie's French-accent corpus
is not mixed into Andreja. This does not change Curie's active B profile or the
owner's Telegram text preference. Andreja's existing persona now explicitly
supports character-inspired original replies while separating fiction from real
memories. Speech training and conversational personality configuration are
separate; changing the persona prompt is not language-model fine-tuning.

Andreja source: [Voice Acting in Games — Andreja dialogue compilation](https://www.youtube.com/watch?v=ssYiZhYuRlM).
The private source manifest records attribution, the 0–30 minute window and a
hash of the decoded audio. 126 clips passed duration/ASR/signal filtering, split
into 113 training and 13 validation clips. Speaker identity comes from the
compilation's attribution; neither transcripts nor speaker purity were manually
verified. The candidate remains experimental. A 120-step Nano LoRA run is
compared against reference conditioning using original evaluation sentences;
neither configuration is installed as Andreja's active `config.json`.

B baseline artifacts: `data/trained-voice/quality-baseline-20261111`. Eight clips:
first cold chunk6.50s; warm median2.31s, no clipping fraction above0.005. ASR found
12 word errors/63 words overall, with most failures in French cases. This is a
baseline, not a French pronunciation fix. Live synthetic smoke timings were
transcription811ms, reply preparation9043ms, first audio7628ms during training.
Do not treat these single observations as latency percentiles or improvements.

Andreja run completed120steps in132s; best validation loss4.3032 versus4.8274
initially (best checkpoint120). Reference-only and strength0.2 adapter comparison
has four matched prompts per variant. Both variants had0/44 ASR word errors and
zero detected clipping across their samples. Listening checks remain pending;
no claim of authentic resemblance, emotional quality or absence of audible echo.
Samples and review template: `experiment-20261111/comparison/` under Andreja's
private directory. Active Curie B and Telegram text mode were verified after
restart.82 focused tests plus7subtests passed; live timing API smoke test passed.

### Separate Andreja Telegram instance (2026-11-11)

Owner requested Andreja running alongside Curie, then clarified she already has a
separate chat. Located `instances/andreja.env` with its existing distinct bot token,
Andreja persona and `.curie_andreja.sqlite3` memory. The CLI instance was stopped.
Started PM2 `andreja-main` for `@andreja_assistant_bot`; Curie remains `curie-main`.
The existing ecosystem now accepts `CURIE_INSTANCE=andreja` from the process
environment, validates the name and instance file, and gives each process a unique
name and logs. Use `CURIE_INSTANCE=andreja pm2 startOrReload ecosystem.config.js
--only andreja-main --update-env`. Do not start the CLI poller at the same time.

Activated `data/trained-voices/andreja/config.json` from the120-step candidate at
the owner's explicit run request. Listening scores remain pending. Voice replies
are enabled for the owner's existing identity in Andreja's database; Curie's text
preference is unchanged. Andreja's instance only starts Telegram, with no API port
or unsolicited proactive scheduler, and shares the existing model gateway.
Sent greeting through Andreja's own bot: message8,7seconds. An earlier greeting
message667 was sent via Curie's bot before clarification; the temporary /andreja
same-chat command was removed. The two final bots use ordinary messages in their
own chats, not personality-switch commands.45 focused tests pass, including PM2
instance isolation; both processes online and PM2 process list saved.

### Superseded: Andreja moved to standalone installation (2026-11-11)

The owner subsequently required complete operational separation. Andreja now runs
from `/home/curlycoffee3808/Desktop/server/bots/andreja-ai`, with independently
copied code, dependencies, models and voice assets, its own `state/memory.sqlite3`,
state directories and `andreja-inference` worker/socket. The old instance file is
marked relocated and this checkout refuses to launch it. Do not follow the
previous same-checkout start command for Andreja. See the standalone installation's
`STANDALONE_DEPLOYMENT.md`. Curie's running processes and text preference remain
unchanged. Final standalone71tests passed; actual LLM reply and voice generation
passed, Telegram voice message9 delivered through Andreja's own bot.
