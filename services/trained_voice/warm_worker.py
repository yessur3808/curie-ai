"""Bounded, isolated recognition worker with cancellation and idle eviction."""

import asyncio
import json
import os
import signal


class WarmRecognitionWorker:
    def __init__(self, command, *, cwd, environment, idle_seconds=120):
        self.command = command
        self.cwd = cwd
        self.environment = environment
        self.idle_seconds = idle_seconds
        self.process = None
        self.lock = asyncio.Lock()
        self.idle_task = None

    async def _stop(self):
        process, self.process = self.process, None
        if process is not None:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()

    async def close(self):
        if self.idle_task is not None:
            self.idle_task.cancel()
            self.idle_task = None
        async with self.lock:
            await self._stop()

    async def _evict(self):
        try:
            await asyncio.sleep(self.idle_seconds)
            async with self.lock:
                await self._stop()
        except asyncio.CancelledError:
            pass

    async def transcribe(self, path, language, *, timeout=110):
        # Cancelling a queued request must not stop another caller's worker.
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
                self.process.stdin.write(
                    (
                        json.dumps({"path": str(path), "language": language}) + "\n"
                    ).encode()
                )
                await self.process.stdin.drain()
                raw = await self.process.stdout.readline()
                if not raw:
                    raise RuntimeError("recognition_worker_exited")
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get("error"):
                    raise RuntimeError("recognition_worker_failed")
                return result
        except BaseException:
            await self._stop()
            raise
        finally:
            self.lock.release()
            if self.process is not None:
                self.idle_task = asyncio.create_task(self._evict())


_workers = {}


def recognition_worker(root, python, environment):
    key = (asyncio.get_running_loop(), str(root), str(python))
    if key not in _workers:
        _workers[key] = WarmRecognitionWorker(
            [str(python), "-m", "services.trained_voice.warm_transcribe"],
            cwd=str(root),
            environment=environment,
        )
    return _workers[key]


async def close_recognition_workers():
    loop = asyncio.get_running_loop()
    for key in list(_workers):
        if key[0] is loop:
            await _workers.pop(key).close()
