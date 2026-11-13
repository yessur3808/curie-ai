"""Private JSON-lines recognition worker; one model, no retained transcripts."""

import json
import sys

from faster_whisper import WhisperModel


def main():
    model = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
        local_files_only=True,
    )
    for line in sys.stdin:
        try:
            request = json.loads(line)
            segments, info = model.transcribe(
                request["path"],
                language=request.get("language"),
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            from .prosody import analyze_file

            cues = analyze_file(request["path"])
            response = {
                "voice_cues": cues,
                "text": " ".join(s.text.strip() for s in segments),
                "language": info.language,
            }
        except Exception:
            response = {"error": "transcription_failed"}
        print(json.dumps(response), flush=True)
        request = response = segments = info = None


if __name__ == "__main__":
    main()
