import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import connectors.telegram as telegram_connector
import memory.local_store as local_store
from memory.users import UserManager


@pytest.fixture(autouse=True)
def _disable_live_startup_cooldown(monkeypatch):
    monkeypatch.setenv("CURIE_STARTUP_NOTIFICATION_COOLDOWN_SECONDS", "0")


def test_local_master_recipient_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    internal_id = local_store.get_or_create_user("telegram", "12345")
    assert UserManager.get_external_id(internal_id, "telegram") == "12345"


def test_awake_notification_goes_to_resolved_master(monkeypatch):
    monkeypatch.setenv("MASTER_USER_ID", "master-internal")
    monkeypatch.setenv("NOTIFY_MASTER_ON_STARTUP", "true")
    monkeypatch.setattr(UserManager, "get_external_id", lambda *args: "98765")
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(telegram_connector.notify_master_awake(SimpleNamespace(bot=bot)))
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 98765
    assert "awake" in bot.send_message.await_args.kwargs["text"].lower()


def test_awake_notification_uses_active_personality_not_curie(monkeypatch):
    monkeypatch.setenv("MASTER_USER_ID", "master-internal")
    monkeypatch.setenv("NOTIFY_MASTER_ON_STARTUP", "true")
    monkeypatch.delenv("CURIE_STARTUP_VARIANT", raising=False)
    monkeypatch.setattr(UserManager, "get_external_id", lambda *args: "98765")
    monkeypatch.setattr(telegram_connector.secrets, "choice", lambda values: values[-1])
    previous = telegram_connector._runtime.workflow
    telegram_connector._runtime.workflow = SimpleNamespace(
        persona={
            "name": "Andreja",
            "startup_messages": [
                "Andreja is online.",
                "I'm connected and ready. We can begin whenever you like.",
            ],
        }
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    try:
        asyncio.run(telegram_connector.notify_master_awake(SimpleNamespace(bot=bot)))
    finally:
        telegram_connector._runtime.workflow = previous
    text = bot.send_message.await_args.kwargs["text"]
    assert text == "I'm connected and ready. We can begin whenever you like."
    assert "Curie" not in text
    assert "Bonjour" not in text


def test_startup_fallback_uses_active_personality_name():
    previous = telegram_connector._runtime.workflow
    telegram_connector._runtime.workflow = SimpleNamespace(persona={"name": "Andreja"})
    try:
        assert (
            telegram_connector._persona_startup_message()
            == "Andreja is awake, online, and ready."
        )
    finally:
        telegram_connector._runtime.workflow = previous


def test_awake_notification_skips_without_master(monkeypatch):
    monkeypatch.delenv("MASTER_USER_ID", raising=False)
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(telegram_connector.notify_master_awake(SimpleNamespace(bot=bot)))
    bot.send_message.assert_not_awaited()


def test_awake_notification_delivery_failure_does_not_break_startup(monkeypatch):
    monkeypatch.setenv("MASTER_USER_ID", "master-internal")
    monkeypatch.setattr(UserManager, "get_external_id", lambda *args: "98765")
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("chat not found"))
    )
    asyncio.run(telegram_connector.notify_master_awake(SimpleNamespace(bot=bot)))
    bot.send_message.assert_awaited_once()


def test_awake_notification_cooldown_suppresses_restart_spam(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_USER_ID", "master-internal")
    monkeypatch.setenv("NOTIFY_MASTER_ON_STARTUP", "true")
    monkeypatch.setenv("CURIE_STARTUP_NOTIFICATION_COOLDOWN_SECONDS", "21600")
    monkeypatch.setenv(
        "CURIE_STARTUP_NOTIFICATION_STATE", str(tmp_path / "startup-notified")
    )
    monkeypatch.setattr(UserManager, "get_external_id", lambda *args: "98765")
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)

    asyncio.run(telegram_connector.notify_master_awake(application))
    asyncio.run(telegram_connector.notify_master_awake(application))

    assert bot.send_message.await_count == 1
