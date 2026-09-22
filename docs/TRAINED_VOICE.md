# Curie trained voice

## Service ownership and startup

The canonical implementation is `services/trained_voice/` in this repository. This includes the conversation-only HTTP API, local speech and transcription workers, persona delivery, streaming/cancellation helpers, dataset preparation, LoRA training and evaluation. The Observatory dashboard is a client. No dashboard checkout is required to run this API after provisioning Curie's models and runtimes.

From the Curie checkout, start `.venv/bin/python -m services.trained_voice.api`, or use `pm2 startOrReload deploy/ecosystem.trained-voice.config.cjs --only curie-dashboard-api --update-env`. The service binds only to `127.0.0.1:8010`; the dashboard retains authenticated HTTPS access. The existing process name is retained for operational compatibility. Starting this service does not start Curie's other bot channels. For the one-time migration from the old dashboard script, first run `pm2 delete curie-dashboard-api`, then `pm2 start deploy/ecosystem.trained-voice.config.cjs --only curie-dashboard-api` and `pm2 save`: PM2 reloads can retain the old script path when changing executables.

`CURIE_ROOT` optionally selects the Curie checkout. `CURIE_VOICE_HOME`, `CURIE_VOICE_PYTHON`, and `CURIE_TRANSCRIBE_PYTHON` can override the default data directory and interpreter paths shown below. Use absolute paths for overrides. Standard-library-only `VoicePaths.ready()` checks model metadata without importing Torch. Synthesis and recognition remain one-shot processes with the existing thread limits and cancellation behavior.

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
