import asyncio
import threading

import pytest

from connectors.lifecycle import (
    BoundedAsyncWorkQueue,
    ConnectorApplication,
    ConnectorRegistry,
    ConnectorState,
    DeliveryStatus,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_bounded_queue_rejects_work_when_full():
    queue = BoundedAsyncWorkQueue(capacity=1)
    gate = asyncio.Event()

    async def held_work():
        await gate.wait()
        return "done"

    first = asyncio.create_task(queue.run(held_work))
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="queue is full"):
        await queue.run(held_work)

    gate.set()
    assert await first == "done"
    assert queue.size == 0


def test_registry_rejects_duplicate_connector_names():
    registry = ConnectorRegistry()
    registry.register(ConnectorApplication("telegram", lambda workflow: None))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(ConnectorApplication("telegram", lambda workflow: None))


def test_connector_readiness_and_health():
    stop = threading.Event()
    started = threading.Event()

    def run(_workflow):
        started.set()
        stop.wait()

    connector = ConnectorApplication(
        "telegram", run, ready_probe=started.is_set, stop_fn=stop.set
    )
    connector.start(object())

    assert connector.wait_ready(timeout=1)
    health = connector.health()
    assert health.state == ConnectorState.READY
    assert health.ready is True
    assert health.thread_alive is True

    connector.stop()
    assert connector.health().state == ConnectorState.STOPPED


def test_failed_connector_reports_error():
    def fail(_workflow):
        raise RuntimeError("startup failed")

    connector = ConnectorApplication("broken", fail)
    connector.start(object()).join(timeout=1)

    health = connector.health()
    assert health.state == ConnectorState.FAILED
    assert health.ready is False
    assert health.error == "startup failed"


@pytest.mark.asyncio
async def test_registry_exposes_only_push_capable_connectors():
    sent = []

    async def send(recipient, message):
        sent.append((recipient, message))
        return True

    registry = ConnectorRegistry()
    push = ConnectorApplication("push", lambda workflow: None, send_fn=send)
    push.thread = threading.Thread()
    push.state = ConnectorState.READY
    push.ready_probe = lambda: True
    registry.register(push)
    registry.register(ConnectorApplication("receive-only", lambda workflow: None))

    outbound = registry.outbound()
    assert list(outbound) == ["push"]
    assert await outbound["push"]("123", "bonjour") is True
    assert sent == [("123", "bonjour")]


@pytest.mark.asyncio
async def test_delivery_failure_returns_a_failed_receipt_not_success():
    async def fail_send(_recipient, _message):
        raise RuntimeError("provider rejected delivery with private details")

    connector = ConnectorApplication(
        "telegram",
        lambda workflow: None,
        send_fn=fail_send,
        ready_probe=lambda: True,
    )
    connector.state = ConnectorState.READY

    receipt = await connector.send_with_receipt("private-recipient", "private text")

    assert receipt.delivered is False
    assert receipt.status is DeliveryStatus.FAILED
    assert receipt.error_type == "RuntimeError"
    assert "private" not in str(receipt.as_dict())
    assert await connector.send("private-recipient", "private text") is False
