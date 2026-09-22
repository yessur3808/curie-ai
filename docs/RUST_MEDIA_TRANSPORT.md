# Rust audio and media transport

## Scope and trust boundary

Curie's second native module owns deterministic, byte-oriented transport work:

- constant-memory file reads with configured size limits;
- SHA-256 hashing, signature sniffing, and declared MIME/extension checks;
- executable-signature and cross-kind mismatch rejection;
- constant-memory concatenation of matching PCM WAV spans;
- isolated child-process stdin/stdout transport with output bounds;
- deadlines, cancellation, forced child reaping, and content-free metrics;
- supervised FFmpeg Opus encoding and trained-voice worker execution.

Python still owns Telegram and other connector presentation, attachment policy,
archive and malware scanning, Whisper, Chatterbox/Torch, local vision, voice
persona choices, and all user-facing prose. Rust never receives owner memory,
credentials, prompts intended for an LLM, or permission to perform external
actions. Trained speech text is passed only to the local isolated inference
child and is never included in transport logs or metrics.

## Build and activation

```sh
make media-transport
CURIE_MEDIA_TRANSPORT=rust .venv/bin/python -c \
  'from services.media_transport import media_transport_status; print(media_transport_status())'
```

The extension uses Python's stable ABI from Python 3.10 onward. Its shared
object is an untracked deployment artifact; Rust source and `Cargo.lock` are
committed.

`CURIE_MEDIA_TRANSPORT` accepts:

- `auto` (default): prefer Rust when installed and otherwise use the bounded
  Python implementation;
- `rust`: require the native transport and degrade readiness if it is absent;
- `python`: force the audited rollback implementation.

Changing the setting requires a Curie restart. It does not change stored
media, voice models, connector credentials, or memory schemas.

## Safety behavior

Both implementations reject empty and oversized inputs, symlinks, executable
signatures, declared media whose confident signature has another kind,
malformed WAV chunks, mismatched PCM formats, excessive combined output,
excessive worker stdout, worker timeouts, and cancelled work. Native inspection
streams in 64 KiB buffers and never loads the configured maximum attachment
into Python memory. WAV concatenation writes through a temporary file in the
destination directory and atomically persists only a complete output.

The process supervisor starts children with an explicit environment, discards
dependency stderr, bounds stdout, polls a monotonic deadline, and kills and
waits for the child on timeout or cancellation. Python cancellation sets a
native atomic token and waits for the reaping path before propagating
`CancelledError`; it does not abandon a model or FFmpeg process.

## Verification and rollback

```sh
make media-transport-check
.venv/bin/python -m scripts.benchmark_media_transport \
  --megabytes 16 --iterations 5 --mode both
```

The blocking CI lane runs Rustfmt, Clippy with warnings denied, Cargo unit
tests, a locked release build, parity and signature rejection tests, malformed
and mismatched WAV tests, timeout and real child-reaping drills, media ingestion
tests, voice delivery tests, and the trained-worker contracts. RustSec audits
the committed native lockfile. The ordinary suite runs without the extension
and therefore keeps the Python rollback continuously tested.

On the production server, a separate-process 64 MiB benchmark completed native
inspection in 16.168 ms versus 22.034 ms for the bounded Python rollback
(1.36x faster), and native PCM concatenation in 14.627 ms versus 18.551 ms
(1.27x faster). Peak process RSS was 27,152 KiB versus 27,748 KiB; both paths
remain constant-memory, so the reliability improvement is more material than
the 2.1% baseline-RSS difference. Against the former FFmpeg concat subprocess,
the native path used 0.05 seconds of CPU instead of 0.25 seconds and avoids a
separate 13,388 KiB codec process for lossless PCM joining. Opus encoding still
uses supervised FFmpeg because replacing a mature codec would add risk without
reducing Curie's trained model memory. These September 2026 measurements are
host-specific evidence, not universal guarantees.

Rollback is data-free: set `CURIE_MEDIA_TRANSPORT=python`, restart Curie, check
that `media_transport_status()["active"] == "python"`, and rerun the same media
corpus. No attachment, model, voice, or database migration is involved.
