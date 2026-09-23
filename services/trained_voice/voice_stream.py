"""Bounded, cancellable sentence events from the isolated local speech worker."""

import asyncio
import json


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
    from services.media_transport import stream_process_lines
    from services.api_voice_runtime import required_native

    native = required_native()

    lines = stream_process_lines(
        command,
        text.encode(),
        environment=env,
        cwd=cwd,
        timeout=timeout,
    )
    try:
        async for line in lines:
            if native is not None:
                encoded = native.parse_voice_event(line)
                if encoded is not None:
                    yield json.loads(encoded)
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue  # Dependency diagnostics are not response content.
            if event.get("type") == "audio":
                yield event
    finally:
        await lines.aclose()
