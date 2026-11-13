"""Private, bounded process lifecycle evidence; never logs payloads or env."""

import atexit
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import time


class _PrivateHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.baseFilename, 0o600)
        return super()._open()


def install_lifecycle_log(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    handler = _PrivateHandler(path, maxBytes=1_000_000, backupCount=3)
    logger = logging.Logger("curie.lifecycle", logging.INFO)
    logger.addHandler(handler)
    started = time.monotonic()

    def record(event, **fields):
        logger.info(
            json.dumps(
                {
                    "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "event": event,
                    "pid": os.getpid(),
                    "ppid": os.getppid(),
                    "uptime_seconds": round(time.monotonic() - started, 3),
                    **fields,
                }
            )
        )

    record("started")
    atexit.register(lambda: record("exiting"))
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(sig)

        def on_signal(signum, frame, previous=previous):
            record("signal", signal=signal.Signals(signum).name)
            if callable(previous):
                return previous(signum, frame)
            if previous == signal.SIG_IGN:
                return
            # Preserve default signal termination and its supervisor exit status.
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        signal.signal(sig, on_signal)
