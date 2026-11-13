"""Private JSON-line speech worker; one model, refreshed when assets change."""

import contextlib
import json
import os
import sys
from types import SimpleNamespace


def main():
    from .reference_voice import synthesize

    os.nice(10)
    protocol = sys.stdout
    for line in sys.stdin:
        try:
            request = json.loads(line)

            # Dependency progress belongs on stderr, only our JSON is protocol.
            class Events:
                def write(self, value):
                    if value.startswith("{"):
                        try:
                            event = json.loads(value)
                        except ValueError:
                            return
                        if event.get("type") == "audio" or "generationSeconds" in event:
                            protocol.write(json.dumps(event) + "\n")
                            protocol.flush()

                def flush(self):
                    pass

            with contextlib.redirect_stdout(Events()):
                synthesize(SimpleNamespace(**request["args"]), request["text"])
            print(json.dumps({"type": "done"}), file=protocol, flush=True)
        except Exception:
            print(
                json.dumps({"type": "error", "error": "trained_voice_worker_failed"}),
                file=protocol,
                flush=True,
            )
            return


if __name__ == "__main__":
    main()
