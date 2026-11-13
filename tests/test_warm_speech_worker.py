import asyncio
import json
import os
import sys

import pytest

from services.trained_voice.warm_speech_worker import WarmSpeechWorker


def worker(tmp_path, idle=120):
    script = tmp_path / "speech.py"
    script.write_text(
        "import sys,json,time,os\nfor line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({'type':'audio','text':r['text'],'pid':os.getpid()}),flush=True)\n if r['text']=='slow': time.sleep(10)\n print(json.dumps({'type':'done'}),flush=True)\n"
    )
    return WarmSpeechWorker(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        environment=dict(os.environ),
        idle_seconds=idle,
    )


@pytest.mark.asyncio
async def test_reuse_and_idle_eviction(tmp_path):
    w = worker(tmp_path, idle=0.03)
    try:
        a = [e async for e in w.events({"text": "first"})]
        b = [e async for e in w.events({"text": "second"})]
        assert a[0]["pid"] == b[0]["pid"]
        assert b[0]["text"] == "second"
        await asyncio.sleep(0.1)
        assert w.process is None
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_stream_close_kills_generation_and_restart_is_clean(tmp_path):
    w = worker(tmp_path)
    stream = w.events({"text": "slow"})
    try:
        first = await anext(stream)
        await stream.aclose()
        assert w.process is None
        fresh = [e async for e in w.events({"text": "fresh"})]
        assert fresh[0]["pid"] != first["pid"]
        assert fresh[0]["text"] == "fresh"
    finally:
        await stream.aclose()
        await w.close()


@pytest.mark.asyncio
async def test_timeout_and_queued_cancellation(tmp_path):
    w = worker(tmp_path)
    active = w.events({"text": "slow"}, timeout=0.15)
    try:
        await anext(active)
        queued = asyncio.create_task(anext(w.events({"text": "queued"})))
        await asyncio.sleep(0.01)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert w.process.returncode is None
        with pytest.raises(TimeoutError):
            await anext(active)
        assert w.process is None
    finally:
        await active.aclose()
        await w.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    ["print('not-json',flush=True)", 'print(\'{"type":"error"}\',flush=True)', "pass"],
)
async def test_broken_worker_is_discarded(tmp_path, response):
    script = tmp_path / "broken.py"
    script.write_text("import sys\nsys.stdin.readline()\n" + response + "\n")
    w = WarmSpeechWorker(
        [sys.executable, str(script)], cwd=str(tmp_path), environment=dict(os.environ)
    )
    try:
        with pytest.raises((RuntimeError, ValueError)):
            async for _ in w.events({"text": "hello"}):
                pass
        assert w.process is None
    finally:
        await w.close()
