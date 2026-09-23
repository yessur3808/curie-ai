import asyncio

import pytest

from llm import inference_service
from llm.inference_service import InferenceOverloaded, ManagedInferenceService

pytestmark = pytest.mark.integration


def operation(name, order, *, gate=None, tokens=()):
    async def run(emit, cancelled):
        if gate is not None:
            await gate.wait()
        if cancelled.is_set():
            raise asyncio.CancelledError
        order.append(name)
        for token in tokens:
            await emit(token)
            await asyncio.sleep(0)
        return None if tokens else name

    return run


@pytest.mark.asyncio
async def test_active_requests_overtake_queued_background_work():
    service = ManagedInferenceService(capacity=4, workers=1)
    order = []
    gate = asyncio.Event()
    running = asyncio.create_task(
        service.submit(operation("running", order, gate=gate), owner_id="a")
    )
    await asyncio.sleep(0)
    background = asyncio.create_task(
        service.submit(
            operation("background", order), owner_id="b", priority="background"
        )
    )
    active = asyncio.create_task(
        service.submit(operation("active", order), owner_id="c", priority="active")
    )
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(running, background, active)
    assert order == ["running", "active", "background"]
    await service.close()


@pytest.mark.asyncio
async def test_queue_is_bounded_and_reports_overload():
    service = ManagedInferenceService(capacity=1, workers=1)
    gate = asyncio.Event()
    first = asyncio.create_task(
        service.submit(operation("first", [], gate=gate), owner_id="a")
    )
    await asyncio.sleep(0)
    queued = asyncio.create_task(service.submit(operation("queued", []), owner_id="b"))
    await asyncio.sleep(0)
    with pytest.raises(InferenceOverloaded):
        await service.submit(operation("rejected", []), owner_id="c")
    gate.set()
    await asyncio.gather(first, queued)
    assert service.snapshot()["overloaded"] == 1
    await service.close()


@pytest.mark.asyncio
async def test_superseded_owner_request_is_cooperatively_cancelled():
    service = ManagedInferenceService(capacity=3, workers=1)
    gate = asyncio.Event()
    old = asyncio.create_task(
        service.submit(operation("old", [], gate=gate), owner_id="same")
    )
    await asyncio.sleep(0)
    new = asyncio.create_task(
        service.submit(operation("new", []), owner_id="same", supersede_owner=True)
    )
    await asyncio.sleep(0)
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await old
    assert (await new).text == "new"
    assert service.snapshot()["cancelled"] == 1
    await service.close()


@pytest.mark.asyncio
async def test_streaming_records_real_first_token_and_throughput():
    service = ManagedInferenceService(capacity=2, workers=1)
    request_id = "stream-request"
    received = []
    async for token in service.stream(
        operation("stream", [], tokens=("hello ", "world")),
        owner_id="a",
        request_id=request_id,
    ):
        received.append(token)
    assert received == ["hello ", "world"]
    assert service.snapshot()["completed"] == 1
    await service.close()


@pytest.mark.asyncio
async def test_close_inference_singleton_cancels_jobs_and_workers(monkeypatch):
    service = ManagedInferenceService(capacity=2, workers=1)
    monkeypatch.setattr(inference_service, "_service", service)
    gate = asyncio.Event()
    pending = asyncio.create_task(
        service.submit(operation("pending", [], gate=gate), owner_id="owner")
    )
    await asyncio.sleep(0)

    await inference_service.close_inference_service()
    await asyncio.sleep(0)

    assert pending.cancelled()
    assert service._worker_tasks == []
    assert service._jobs == {}
    assert inference_service._service is None
