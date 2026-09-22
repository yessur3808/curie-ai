import asyncio

import pytest

pytest.importorskip("_curie_connector_gateway")

from connectors.delivery_gateway import (
    DeliveryQueue,
    DeliveryQueueExpired,
    DuplicateDelivery,
    connector_gateway_metrics,
    connector_gateway_status,
    retry_delay_ms,
)
from connectors.lifecycle import ConnectorApplication, ConnectorState, DeliveryStatus


@pytest.fixture(autouse=True)
def require_rust(monkeypatch):
    monkeypatch.setenv("CURIE_CONNECTOR_GATEWAY", "rust")
    connector_gateway_metrics(reset=True)


def test_strict_status_reports_native_without_fallback():
    status = connector_gateway_status()
    assert status == {
        "mode": "rust",
        "available": True,
        "active": "rust",
        "version": "rust-connector-gateway-v1",
        "fallback": False,
        "import_error": None,
    }


@pytest.mark.asyncio
async def test_native_queue_applies_priority_then_fifo_after_active_delivery():
    queue = DeliveryQueue(capacity=4, concurrency=1)
    gate = asyncio.Event()
    order = []

    async def blocker():
        order.append("blocker")
        await gate.wait()

    async def record(name):
        order.append(name)
        return name

    first = asyncio.create_task(queue.run(blocker))
    await asyncio.sleep(0.01)
    low = asyncio.create_task(queue.run(lambda: record("low"), priority=0))
    urgent = asyncio.create_task(queue.run(lambda: record("urgent"), priority=5))
    await asyncio.sleep(0.01)
    assert queue.snapshot()["pending"] == 2
    gate.set()

    assert await asyncio.gather(first, low, urgent) == [None, "low", "urgent"]
    assert order == ["blocker", "urgent", "low"]
    assert queue.snapshot()["completed"] == 3


@pytest.mark.asyncio
async def test_deadline_expires_without_executing_provider_call():
    queue = DeliveryQueue(capacity=2, concurrency=1)
    gate = asyncio.Event()
    called = False

    async def blocker():
        await gate.wait()

    async def forbidden():
        nonlocal called
        called = True

    first = asyncio.create_task(queue.run(blocker))
    await asyncio.sleep(0.01)
    with pytest.raises(DeliveryQueueExpired):
        await queue.run(forbidden, timeout_seconds=0.005)
    assert called is False
    gate.set()
    await first


@pytest.mark.asyncio
async def test_cancellation_reclaims_capacity_and_duplicate_key_is_rejected():
    queue = DeliveryQueue(capacity=1, concurrency=1)
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()

    first = asyncio.create_task(queue.run(blocked, idempotency_key="delivery-1"))
    await asyncio.sleep(0.01)
    with pytest.raises(DuplicateDelivery):
        await queue.run(blocked, idempotency_key="delivery-1")
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert queue.snapshot()["size"] == 0
    assert await queue.run(lambda: asyncio.sleep(0, result=True)) is True


@pytest.mark.asyncio
async def test_receipt_reports_native_queue_and_safe_idempotent_retry():
    calls = 0

    async def send(_recipient, _message):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("private provider response")
        return True

    connector = ConnectorApplication(
        "telegram",
        lambda workflow: None,
        send_fn=send,
        ready_probe=lambda: True,
        queue_concurrency=1,
    )
    connector.state = ConnectorState.READY
    receipt = await connector.send_with_receipt(
        "private-recipient",
        "private message",
        idempotency_key="safe-delivery-key",
        max_attempts=2,
    )

    assert receipt.status is DeliveryStatus.DELIVERED
    assert receipt.delivered is True
    assert receipt.attempts == 2
    assert receipt.gateway_backend == "rust"
    assert receipt.queue_wait_ms >= 0
    assert "private" not in str(receipt.as_dict())


def test_retry_delay_is_bounded_deterministic_and_honors_provider_delay():
    assert retry_delay_ms(3, jitter_seed=7) == retry_delay_ms(3, jitter_seed=7)
    assert 0 <= retry_delay_ms(3, base_ms=100, cap_ms=500, jitter_seed=7) <= 400
    assert retry_delay_ms(2, retry_after_ms=900, cap_ms=500) == 500


def test_python_rollback_retains_the_same_queue_contract(monkeypatch):
    monkeypatch.setenv("CURIE_CONNECTOR_GATEWAY", "python")
    queue = DeliveryQueue(capacity=3, concurrency=2)
    assert queue.backend == "python"
    assert queue.snapshot() == {
        "pending": 0,
        "active": 0,
        "size": 0,
        "capacity": 3,
        "concurrency": 2,
        "saturated": False,
        "rejected": 0,
        "expired": 0,
        "cancelled": 0,
        "completed": 0,
        "duplicates": 0,
        "backend": "python",
    }
