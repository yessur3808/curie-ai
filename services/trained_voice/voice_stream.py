"""Bounded, cancellable sentence events from the isolated local speech worker."""

import asyncio
import json
import time


async def until_disconnect(request, awaitable):
    """Ordinary JSON routes also stop expensive work when their client leaves."""
    task = asyncio.ensure_future(awaitable)

    async def watch():
        while not await request.is_disconnected():
            await asyncio.sleep(0.25)

    disconnected = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait(
            (task, disconnected), return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            return await task
        raise asyncio.CancelledError()
    finally:
        for pending in (task, disconnected):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, disconnected, return_exceptions=True)


async def worker_events(command, text, *, env=None, cwd=None, timeout=115):
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        cwd=cwd,
    )
    deadline = time.monotonic() + timeout
    try:
        proc.stdin.write(text.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        while True:
            line = await asyncio.wait_for(
                proc.stdout.readline(), max(0.01, deadline - time.monotonic())
            )
            if not line:
                break
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue  # Dependency diagnostics are not response content.
            if event.get("type") == "audio":
                yield event
        await asyncio.wait_for(proc.wait(), max(0.01, deadline - time.monotonic()))
        if proc.returncode:
            raise RuntimeError("Speech worker failed")
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
