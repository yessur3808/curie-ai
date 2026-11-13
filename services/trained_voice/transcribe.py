"""One-shot, low-thread-count local speech recognition worker."""

import json
import sys
from faster_whisper import WhisperModel

from .prosody import analyze_file

model = WhisperModel(
    "base", device="cpu", compute_type="int8", cpu_threads=2, num_workers=1
)
segments, info = model.transcribe(
    sys.argv[1],
    language=sys.argv[2] if len(sys.argv) > 2 else None,
    beam_size=1,
    vad_filter=True,
)
cues = analyze_file(sys.argv[1])
print(
    json.dumps(
        {
            "voice_cues": cues,
            "text": " ".join(s.text.strip() for s in segments),
            "language": info.language,
        }
    )
)
