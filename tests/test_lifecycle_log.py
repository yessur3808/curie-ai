import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


def test_rotation_keeps_files_private(tmp_path):
    from utils.lifecycle_log import _PrivateHandler
    import logging

    handler = _PrivateHandler(tmp_path / "events", maxBytes=80, backupCount=2)
    try:
        for _ in range(20):
            handler.emit(
                logging.LogRecord("test", 20, "", 0, "lifecycle event", (), None)
            )
    finally:
        handler.close()
    assert len(list(tmp_path.iterdir())) == 3
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in tmp_path.iterdir())


@pytest.mark.parametrize("termination", [None, signal.SIGTERM, signal.SIGINT])
def test_lifecycle_subprocess_logs_without_changing_signal_behavior(
    tmp_path, termination
):
    path = tmp_path / "lifecycle.jsonl"
    code = (
        "from utils.lifecycle_log import install_lifecycle_log; "
        "import os, signal; "
        f"install_lifecycle_log({str(path)!r}); "
        + (f"os.kill(os.getpid(), {int(termination)})" if termination else "pass")
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=10,
        cwd=Path(__file__).parents[1],
    )
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["event"] == "started"
    assert path.stat().st_mode & 0o777 == 0o600
    assert all(
        set(row) <= {"utc", "event", "pid", "ppid", "uptime_seconds", "signal"}
        for row in rows
    )
    if termination:
        assert result.returncode == -int(termination)
        assert rows[1]["signal"] == signal.Signals(termination).name
    else:
        assert result.returncode == 0
        assert rows[-1]["event"] == "exiting"
