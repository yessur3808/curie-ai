"""Bounded speech process reuse; cancellation kills active generation."""

import asyncio
import json

from .warm_worker import WarmRecognitionWorker


class WarmSpeechWorker(WarmRecognitionWorker):
    async def events(self, request, *, timeout=115):
        await asyncio.wait_for(self.lock.acquire(), timeout=3)
        try:
            if self.idle_task is not None:
                self.idle_task.cancel()
                self.idle_task = None
            async with asyncio.timeout(timeout):
                if self.process is None or self.process.returncode is not None:
                    await self._stop()
                    self.process = await asyncio.create_subprocess_exec(
                        *self.command,
                        cwd=self.cwd,
                        env=self.environment,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        start_new_session=True,
                        limit=64 * 1024,
                    )
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                await self.process.stdin.drain()
                while True:
                    raw = await self.process.stdout.readline()
                    if not raw:
                        raise RuntimeError("trained_voice_worker_exited")
                    event = json.loads(raw)
                    if not isinstance(event, dict) or event.get("type") == "error":
                        raise RuntimeError("trained_voice_worker_failed")
                    if event.get("type") == "done":
                        break
                    yield event
        except BaseException:
            await self._stop()
            raise
        finally:
            self.lock.release()
            if self.process is not None:
                self.idle_task = asyncio.create_task(self._evict())


_workers = {}


def speech_worker(paths, environment):
    # One resident model per application process, not one per persona.
    key = (asyncio.get_running_loop(), str(paths.root), str(paths.speech_python))
    if key not in _workers:
        _workers[key] = WarmSpeechWorker(
            [str(paths.speech_python), "-m", "services.trained_voice.warm_synthesize"],
            cwd=str(paths.root),
            environment=environment,
            idle_seconds=60,
        )
    return _workers[key]


async def close_speech_workers():
    loop = asyncio.get_running_loop()
    for key in list(_workers):
        if key[0] is loop:
            await _workers.pop(key).close()
