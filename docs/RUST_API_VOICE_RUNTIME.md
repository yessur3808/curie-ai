# Rust API and Voice Runtime

Curie's API and voice hot paths use one coarse-grained PyO3 ABI3 extension at
`native/api_voice_runtime`. The boundary is intentionally larger than a set of
small helper functions: Rust owns the mutable state machines, while Python is a
thin framework and model adapter.

## Ownership boundary

Rust owns:

- bounded global and per-owner API request admission;
- request leases, fencing tokens, deadlines, cancellation, and idempotent
  response replay;
- priority/FIFO inference ordering, queue saturation, owner cancellation, and
  content-free metrics;
- live voice-session identity, owner binding, TTL expiry, state transitions,
  active-operation fencing, bounded history, and generated-artifact expiry;
- audio-file validation, language/accent normalization, Faster-Whisper worker
  plans, and transcript validation;
- trained-voice readiness, synthesis command construction, text validation,
  cross-process model leases, safe metrics, and stream-event validation.

Python continues to own FastAPI/ASGI presentation, connector identity mapping,
Curie's personality and wording, authorization, the chat workflow, and model
SDK calls. Faster-Whisper and Chatterbox Nano stay in isolated Python workers
because their supported inference stacks are Python/PyTorch. The existing Rust
media transport owns their process supervision, bytes, deadlines, bounded
stdout, and cancellation.

## Live voice state machine

Each owner/session has exactly one active operation. Rust issues a monotonic
fencing token for that operation. Supported states are `idle`, `listening`,
`transcribing`, `thinking`, `synthesizing`, `speaking`, and `closing`.
Invalid transitions and stale completion tokens fail closed. Disconnects cancel
the Python awaitable and its managed inference request; explicit session cancel
also cancels the matching inference request before invalidating the native
token.

Dashboard clients may open, inspect, cancel, or close sessions through the
`/voice-sessions` routes. Chat, speech, streaming speech, and transcription
accept or return a `session_id`, allowing a client to preserve a natural spoken
conversation without a process-global semaphore or unbounded global history.

## Recognition and synthesis workers

Recognition uses the project `.transcription-venv` Faster-Whisper worker. Rust
validates a regular bounded audio file, normalizes its language hint, constructs
the fixed internal command, and validates the final JSON result. It does not
accept arbitrary commands or environment variables.

Trained synthesis uses the provisioned `.trained-voice-venv` Chatterbox worker.
Rust checks the configured profile/model/adapter files, builds the fixed
command, enforces one cross-process model lease, filters metrics, and validates
streamed audio events. Spoken content is passed only on stdin and is never
included in health or coordination metrics.

## Modes and rollback

`CURIE_API_VOICE_RUNTIME=rust` is the production mode and fails closed if the
extension is unavailable. `auto` prefers Rust but permits the compatibility
path. `python` is the explicit audited rollback used by the generic unit-test
lane. Production should also keep `CURIE_MEDIA_TRANSPORT=rust`, otherwise model
worker process mechanics use the slower Python compatibility implementation.

Build and verify:

```bash
make api-voice-runtime
make api-voice-runtime-check
```

The blocking CI lane runs Rust formatting, Clippy with warnings denied, Cargo
tests, an ABI3 wheel build, strict-native Python integration tests, and the Rust
dependency audit. Runtime health reports the selected backend without exposing
requests, transcripts, voice text, or credentials.
