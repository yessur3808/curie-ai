import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest

from connectors import telegram


def test_split_telegram_message_preserves_content_and_limits_chunks():
    text = ("A useful paragraph with several words. " * 180).strip()
    chunks = telegram.split_telegram_message(text, limit=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert " ".join(chunks).replace("  ", " ") == text


def test_long_telegram_answer_is_split_into_separate_readable_messages():
    text = "\n\n".join(
        f"Section {index}: " + "useful detail " * 18 for index in range(8)
    )
    chunks = telegram.split_telegram_message(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1400 for chunk in chunks)


def test_split_keeps_urls_and_code_spans_intact():
    url = "https://example.com/" + ("path-segment/" * 25) + "?a=1&b=2"
    code = "`device_registry.resolve('dreamview')`"
    text = ("Useful context. " * 25) + url + " " + code + (" More detail." * 30)

    chunks = telegram.split_telegram_message(text, limit=500, preferred_limit=400)

    assert any(url in chunk for chunk in chunks)
    assert any(code in chunk for chunk in chunks)
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_reply_in_chunks_uses_safe_telegram_html():
    replies = []

    async def reply_text(text, parse_mode=None):
        replies.append((text, parse_mode))

    message = SimpleNamespace(reply_text=reply_text)
    asyncio.run(telegram.reply_in_chunks(message, "**Ready**\n- Lamp is off"))

    assert replies == [("<b>Ready</b>\n• Lamp is off", "HTML")]


def test_reply_with_result_sends_deliberate_parts_as_separate_messages():
    replies = []

    async def reply_text(text, parse_mode=None):
        replies.append((text, parse_mode))

    message = SimpleNamespace(reply_text=reply_text)
    result = {
        "text": "First result\n\nSecond result",
        "message_parts": ["**First result**", "*Second result*"],
    }
    asyncio.run(telegram.reply_with_result(message, result, "unavailable"))

    assert replies == [
        ("<b>First result</b>", "HTML"),
        ("<i>Second result</i>", "HTML"),
    ]


def test_typing_heartbeat_refreshes_until_cancelled(monkeypatch):
    actions = []

    class Bot:
        async def send_chat_action(self, **kwargs):
            actions.append(kwargs)

    async def cancel_after_first_refresh(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(telegram.asyncio, "sleep", cancel_after_first_refresh)
    message = SimpleNamespace(chat_id=42, get_bot=lambda: Bot())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(telegram._typing_heartbeat(message))

    assert actions == [{"chat_id": 42, "action": "typing"}]


def test_proactive_send_uses_client_owned_by_calling_loop(monkeypatch):
    observed = []

    class OutboundBot:
        def __init__(self, token):
            self.token = token

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_message(self, **kwargs):
            observed.append(kwargs)

    class OtherLoop:
        def is_running(self):
            return True

    monkeypatch.setattr(telegram, "Bot", OutboundBot)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    previous_app, previous_loop = telegram._runtime.application, telegram._runtime.loop
    telegram._runtime.application = SimpleNamespace(bot=SimpleNamespace())
    telegram._runtime.loop = OtherLoop()
    try:
        assert asyncio.run(telegram.send_message("42", "hello")) is True
        assert observed == [{"chat_id": 42, "text": "hello"}]
    finally:
        telegram._runtime.application = previous_app
        telegram._runtime.loop = previous_loop


def test_telegram_shutdown_closes_intake_inference_and_application(monkeypatch):
    events = []

    class Updater:
        running = True

        async def stop(self):
            events.append("updater")

    class Application:
        running = True
        updater = Updater()

        async def stop(self):
            events.append("application")

    async def close_inference():
        events.append("inference")

    monkeypatch.setattr(
        "llm.inference_service.close_inference_service", close_inference
    )
    previous = telegram._runtime.application
    telegram._runtime.application = Application()
    try:
        asyncio.run(telegram._shutdown_telegram_runtime())
    finally:
        telegram._runtime.application = previous

    assert events == ["updater", "inference", "application"]


def test_voice_off_command_has_dedicated_telegram_handler(tmp_path, monkeypatch):
    from memory import local_store

    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    monkeypatch.setattr(telegram, "get_internal_id", lambda *_args: "u1")
    replies = []

    async def reply_text(text):
        replies.append(text)

    user = SimpleNamespace(id=42, username="owner")
    update = SimpleNamespace(
        effective_user=user,
        message=SimpleNamespace(reply_text=reply_text),
    )
    context = SimpleNamespace(args=["off"])
    asyncio.run(telegram.handle_voice_command(update, context))
    assert replies == ["Voice replies are now disabled on telegram."]


def test_poll_command_creates_native_telegram_poll():
    polls = []

    async def reply_poll(**kwargs):
        polls.append(kwargs)

    update = SimpleNamespace(message=SimpleNamespace(reply_poll=reply_poll))
    context = SimpleNamespace(args=["Best", "snack?", "|", "Fruit", "|", "Cake"])
    asyncio.run(telegram.handle_poll(update, context))
    assert polls == [{"question": "Best snack?", "options": ["Fruit", "Cake"]}]


def test_transient_telegram_error_is_handled_without_traceback(caplog):
    class NetworkError(Exception):
        pass

    caplog.set_level(logging.WARNING, logger="connectors.telegram")
    context = SimpleNamespace(error=NetworkError("Bad Gateway"))
    asyncio.run(telegram.handle_telegram_error(None, context))
    assert "polling will retry" in caplog.text


def test_text_reply_to_photo_reprocesses_referenced_attachment(monkeypatch):
    prepared = []
    processed = []

    class TelegramFile:
        async def download_to_drive(self, path):
            from pathlib import Path

            Path(path).write_bytes(b"image")

    media = SimpleNamespace(
        file_id="photo1",
        file_unique_id="unique1",
        file_size=5,
        get_file=lambda: _async_value(TelegramFile()),
    )

    async def prepare(path, filename, request, **_kwargs):
        prepared.append((filename, request))
        return "attachment context"

    async def process(update, text, internal_id, **kwargs):
        processed.append((text, internal_id, kwargs.get("attachments")))

    monkeypatch.setattr(telegram, "get_internal_id", lambda *_args: "u1")
    monkeypatch.setattr("services.media_ingestion.prepare_attachment_message", prepare)
    monkeypatch.setattr(telegram, "_process_and_reply", process)
    previous = telegram._runtime.workflow
    telegram._runtime.workflow = SimpleNamespace(persona={})
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner"),
        voice=None,
        text="Explain it to me in text",
        reply_to_message=SimpleNamespace(photo=[media], document=None),
        chat_id=7,
        reply_text=lambda _text: None,
    )
    try:
        asyncio.run(
            telegram.handle_message(SimpleNamespace(message=message), SimpleNamespace())
        )
    finally:
        telegram._runtime.workflow = previous
    assert prepared == [("telegram_reply_unique1.jpg", "Explain it to me in text")]
    assert processed == [
        (
            "attachment context",
            "u1",
            [
                {
                    "id": "unique1",
                    "kind": "image",
                    "filename": "telegram_reply_unique1.jpg",
                    "content_type": "image/jpeg",
                    "file_size": 5,
                    "source": "telegram_reply",
                }
            ],
        )
    ]


async def _async_value(value):
    return value
