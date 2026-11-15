"""Menu cleanup must not make existing commands disappear from the transport."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors import telegram
from services.voice_commands import VOICE_HELP


def test_menu_is_short_unique_and_every_command_is_registered():
    names = [name for name, _ in telegram.COMMAND_MENU]
    assert len(names) == len(set(names)) == 12
    assert set(names) <= telegram.command_handlers().keys()
    assert {"help", "sources", "health", "proactive"} <= set(names)
    assert not {"identify", "clear_memory", "voice_accent", "voice_help"} & set(names)


def test_advanced_help_covers_all_handlers_without_duplicates():
    documented = [name for _, names in telegram.COMMAND_HELP_GROUPS for name in names]
    assert len(documented) == len(set(documented))
    assert set(documented) == telegram.command_handlers().keys()


def test_existing_command_bindings_are_preserved():
    handlers = telegram.command_handlers()
    for name in telegram.WORKFLOW_COMMANDS:
        assert handlers[name] is telegram.handle_workflow_command
    for name in (
        "start",
        "whoami",
        "identify",
        "busy",
        "resume",
        "remember",
        "reset",
        "history",
        "reminders",
        "poll",
        "clear_memory",
    ):
        assert handlers[name] is getattr(telegram, f"handle_{name}")
    for name in ("profile", "accent", "speed", "warmth", "expression"):
        assert handlers[f"voice_{name}"] is telegram.handle_voice_setting_command
    for name in ("sample", "custom", "help"):
        assert handlers[f"voice_{name}"] is getattr(telegram, f"handle_voice_{name}")
    assert handlers["voice"] is telegram.handle_voice_command


def test_startup_registers_commands_before_unknown_command_fallback(monkeypatch):
    # Other connector tests may stub telegram.ext during collection. Keep these
    # constructor spies local so registration checks are independent of test order.
    command_factory = MagicMock(
        side_effect=lambda name, callback: SimpleNamespace(
            command=name, callback=callback
        )
    )
    message_factory = MagicMock(
        side_effect=lambda filters, callback: SimpleNamespace(
            filters=filters, callback=callback
        )
    )
    monkeypatch.setattr(telegram, "CommandHandler", command_factory)
    monkeypatch.setattr(telegram, "MessageHandler", message_factory)
    builder = MagicMock()
    builder.token.return_value = builder
    builder.post_init.return_value = builder
    builder.post_shutdown.return_value = builder
    application = builder.build.return_value
    monkeypatch.setattr(telegram, "ApplicationBuilder", lambda: builder)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token-not-used-for-network")
    monkeypatch.setattr(telegram, "_runtime", telegram.TelegramRuntime())

    telegram.start_telegram_bot(SimpleNamespace(persona={"name": "Curie"}))

    registered = [call.args[0] for call in application.add_handler.call_args_list]
    commands = [handler for handler in registered if hasattr(handler, "command")]
    bindings = {handler.command: handler.callback for handler in commands}
    assert bindings == telegram.command_handlers()
    assert len(commands) == len(bindings)
    fallback = registered[len(commands)]
    assert fallback.filters is telegram.filters.COMMAND
    assert fallback.callback is telegram.handle_unknown_command


def test_startup_publishes_concise_menu_even_without_startup_notification(monkeypatch):
    monkeypatch.setenv("NOTIFY_MASTER_ON_STARTUP", "false")
    monkeypatch.setattr(telegram, "_runtime", telegram.TelegramRuntime())
    bot = SimpleNamespace(set_my_commands=AsyncMock(), send_message=AsyncMock())
    asyncio.run(telegram.notify_master_awake(SimpleNamespace(bot=bot)))
    published = bot.set_my_commands.await_args.args[0]
    assert [(item.command, item.description) for item in published] == list(
        telegram.COMMAND_MENU
    )
    bot.send_message.assert_not_awaited()


def test_help_is_complete_and_available_without_workflow(monkeypatch):
    monkeypatch.setattr(telegram, "_runtime", telegram.TelegramRuntime())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    asyncio.run(telegram.handle_help(update, SimpleNamespace(args=[])))
    text = update.message.reply_text.await_args.args[0]
    assert len(text) < 4096
    assert set(re.findall(r"/([a-z_]+)", text)) == telegram.command_handlers().keys()
    assert "not a complete personal-data erasure" in text


@pytest.mark.parametrize(
    "handler,args",
    [
        (telegram.handle_voice_help, []),
        (telegram.handle_voice_command, ["help"]),
        (telegram.handle_help, ["voice"]),
    ],
)
def test_voice_help_paths_share_one_guide_without_changing_preferences(
    monkeypatch, handler, args
):
    def no_identity_write(*_args):
        raise AssertionError("Help must not touch identity or preferences")

    monkeypatch.setattr(telegram, "get_internal_id", no_identity_write)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    asyncio.run(handler(update, SimpleNamespace(args=args)))
    update.message.reply_text.assert_awaited_once_with(VOICE_HELP)


def test_unknown_command_points_to_help():
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    asyncio.run(telegram.handle_unknown_command(update, SimpleNamespace()))
    assert "/help" in update.message.reply_text.await_args.args[0]


@pytest.mark.parametrize("recipient,replies", [("OtherBot", 0), ("CurieBot", 1)])
def test_unknown_command_respects_bot_address_in_groups(recipient, replies):
    update = SimpleNamespace(
        message=SimpleNamespace(text=f"/unknown@{recipient}", reply_text=AsyncMock())
    )
    context = SimpleNamespace(bot=SimpleNamespace(username="curiebot"))
    asyncio.run(telegram.handle_unknown_command(update, context))
    assert update.message.reply_text.await_count == replies
