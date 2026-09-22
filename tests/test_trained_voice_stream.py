import asyncio
import json
import os
import pathlib
import sys
import tempfile
import unittest
from services.trained_voice.voice_stream import worker_events, until_disconnect


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_route_disconnect_cancels_pending_model_work(self):
        class Request:
            async def is_disconnected(self):
                return True

        stopped = asyncio.Event()

        async def work():
            try:
                await asyncio.sleep(30)
            finally:
                stopped.set()

        with self.assertRaises(asyncio.CancelledError):
            await until_disconnect(Request(), work())
        self.assertTrue(stopped.is_set())

    async def test_chunks_are_available_before_process_exits(self):
        command = [
            sys.executable,
            "-u",
            "-c",
            "import json,time;print(json.dumps({'type':'audio','url':'/audio/voice_abc.wav'}));time.sleep(30)",
        ]
        stream = worker_events(command, "Hello", timeout=3)
        event = await asyncio.wait_for(stream.__anext__(), 2)
        self.assertEqual(event["type"], "audio")
        await stream.aclose()

    async def test_disconnect_kills_the_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = pathlib.Path(directory) / "pid"
            code = (
                "import os,pathlib,json,time;pathlib.Path("
                + repr(str(pidfile))
                + ").write_text(str(os.getpid()));print(json.dumps({'type':'audio'}));time.sleep(30)"
            )
            stream = worker_events([sys.executable, "-u", "-c", code], "Hello")
            await stream.__anext__()
            pid = int(pidfile.read_text())
            await stream.aclose()
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    async def test_worker_failure_and_timeout_are_not_success(self):
        with self.assertRaises(RuntimeError):
            async for _ in worker_events(
                [sys.executable, "-c", "raise SystemExit(2)"], "Hello"
            ):
                pass
        with self.assertRaises(asyncio.TimeoutError):
            async for _ in worker_events(
                [sys.executable, "-c", "import time;time.sleep(30)"],
                "Hello",
                timeout=0.1,
            ):
                pass


if __name__ == "__main__":
    unittest.main()
