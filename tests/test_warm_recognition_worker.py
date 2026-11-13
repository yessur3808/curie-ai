import asyncio
import json
import os
import sys

import pytest

from services.trained_voice.warm_worker import WarmRecognitionWorker


def worker(tmp_path, *, idle=120):
    script = tmp_path / "worker.py"
    script.write_text(
        "import sys,json,time,os\nfor line in sys.stdin:\n r=json.loads(line)\n if r['path']=='slow': time.sleep(10)\n print(json.dumps({'text':r['path'],'language':'en','pid':os.getpid()}),flush=True)\n"
    )
    return WarmRecognitionWorker(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        environment=dict(os.environ),
        idle_seconds=idle,
    )


@pytest.mark.asyncio
async def test_reuses_process_without_leaking_previous_response(tmp_path):
    w = worker(tmp_path)
    try:
        a = await w.transcribe("first", "en")
        b = await w.transcribe("second", "en")
        assert a["pid"] == b["pid"]
        assert b["text"] == "second"
    finally:
        await w.close()
    assert w.process is None


@pytest.mark.asyncio
async def test_timeout_kills_worker_and_next_request_restarts(tmp_path):
    w = worker(tmp_path)
    with pytest.raises(TimeoutError):
        await w.transcribe("slow", "en", timeout=0.1)
    assert w.process is None
    try:
        assert (await w.transcribe("recovered", "en"))["text"] == "recovered"
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_idle_worker_releases_memory(tmp_path):
    w = worker(tmp_path, idle=0.03)
    await w.transcribe("hello", "en")
    await asyncio.sleep(0.1)
    assert w.process is None
    await w.close()


@pytest.mark.asyncio
async def test_cancel_queued_request_does_not_kill_active_worker(tmp_path):
    w = worker(tmp_path)
    active = asyncio.create_task(w.transcribe("slow", "en"))
    await asyncio.sleep(0.05)
    queued = asyncio.create_task(w.transcribe("queued", "en"))
    await asyncio.sleep(0.02)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert w.process.returncode is None
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert w.process is None
    await w.close()
