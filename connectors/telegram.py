# connectors/telegram.py
"""
Telegram connector - transport-only concerns.
Receives Telegram events, normalizes to standard format, calls ChatWorkflow.
"""

import asyncio
from contextlib import suppress
import datetime
import os
import logging
import secrets
import hashlib
from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
import time
from typing import Optional
from dotenv import load_dotenv

from telegram import Bot, BotCommand, Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)
from agent.chat_workflow import ChatWorkflow

from utils.session import set_busy_temporarily, clear_user_busy
from utils.formatting import strip_markdown, telegram_html
from memory import UserManager
from memory.session_store import get_session_manager
from utils.db import is_master_user

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class TelegramRuntime:
    workflow: Optional[ChatWorkflow] = None
    application: object = None
    loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def ready(self) -> bool:
        return self.application is not None and bool(
            getattr(self.application, "running", False)
        )


_runtime = TelegramRuntime()
user_persona_map = {}
user_session_map = {}


@dataclass
class PendingAttachment:
    path: str
    kind: str
    filename: str
    created_at: float


_pending_attachments: dict[int, PendingAttachment] = {}
_pending_lock = threading.Lock()
_ATTACHMENT_TTL_SECONDS = 900
_TYPING_ACTION = "typing"
_STARTUP_NOTIFICATION_LOCK = threading.Lock()

# Telegram's ``filters.COMMAND`` excludes slash commands from the generic text
# handler. Keep every workflow-owned command registered here so it remains
# reachable instead of being silently discarded by the transport.
WORKFLOW_COMMANDS = (
    "agenda",
    "birthday",
    "task",
    "audit",
    "privacy",
    "security",
    "health",
    "readiness",
    "capabilities",
    "proactive",
    "adaptation",
    "home",
    "gmail",
    "x",
    "twitter",
    "browser",
    "memory",
    "skill",
    "approve",
    "reject",
    "reset_preferences",
    "status",
    "metrics",
    "tasks",
    "doctor",
    "logs",
    "stop",
    "restart",
)


def set_workflow(workflow: ChatWorkflow):
    """Set the shared ChatWorkflow instance (called from main.py)."""
    _runtime.workflow = workflow


def is_ready() -> bool:
    return _runtime.ready


async def send_message(
    external_user_id: str,
    message: str,
    parse_mode: Optional[str] = None,
) -> bool:
    """
    Send a proactive message to a Telegram user by their Telegram user ID.

    Used by ProactiveMessagingService to deliver due reminders and check-ins.
    Returns True if the message was sent successfully, False otherwise.

    The optional `parse_mode` parameter allows callers to enable Markdown or HTML
    formatting (e.g. "MarkdownV2", "HTML"). Callers are responsible for
    properly escaping any user-derived content before enabling formatting.
    """
    if _runtime.application is None:
        logger.warning("Telegram app not initialized; cannot send proactive message")
        return False

    async def deliver(bot) -> None:
        if parse_mode:
            await bot.send_message(
                chat_id=int(external_user_id),
                text=message,
                parse_mode=parse_mode,
            )
        else:
            await bot.send_message(
                chat_id=int(external_user_id),
                text=message,
            )

    try:
        current_loop = asyncio.get_running_loop()
        target_loop = _runtime.loop
        if target_loop and target_loop.is_running() and target_loop is not current_loop:
            # The Application bot's HTTP client belongs to the polling loop.
            # A separate short-lived bot keeps proactive delivery entirely on
            # the caller's loop instead of closing the polling transport.
            async with Bot(token=os.environ["TELEGRAM_BOT_TOKEN"]) as outbound_bot:
                await deliver(outbound_bot)
        else:
            await deliver(_runtime.application.bot)
        return True
    except Exception as exc:
        logger.error(
            "Failed to send Telegram proactive message to %s: %s",
            external_user_id,
            exc,
        )
        return False


def get_internal_id(
    tg_user_id: int, telegram_username: str, platform: str = "telegram"
) -> str:
    """
    Get internal user ID, respecting /identify command if used.
    """
    if tg_user_id in user_session_map:
        return user_session_map[tg_user_id]

    return UserManager.get_or_create_user_internal_id(
        channel=platform,
        external_id=tg_user_id,
        secret_username=telegram_username,
        updated_by="telegram_bot",
    )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _runtime.workflow:
        await update.message.reply_text("❌ System not initialized.")
        return

    user = update.effective_user
    if user:
        get_internal_id(user.id, user.username or f"telegram_{user.id}")
    greeting = _runtime.workflow.persona.get("greeting", "Hello!")
    await update.message.reply_text(f"{greeting}")


async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a Telegram poll from `/poll Question | Option 1 | Option 2`."""
    if not update.message:
        return
    raw = " ".join(context.args or []).strip()
    parts = [part.strip() for part in raw.split("|") if part.strip()]
    if len(parts) < 3:
        await update.message.reply_text(
            "Use `/poll Question | Option 1 | Option 2` (up to 10 options).",
            parse_mode="Markdown",
        )
        return
    question, options = parts[0], parts[1:]
    if (
        len(question) > 300
        or len(options) > 10
        or any(len(option) > 100 for option in options)
    ):
        await update.message.reply_text(
            "Polls support a 300-character question and 2 to 10 options of up to 100 characters each."
        )
        return
    await update.message.reply_poll(question=question, options=options)


async def handle_telegram_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Keep transient Telegram transport failures concise while retaining retries."""
    error = getattr(context, "error", None)
    error_types = {base.__name__ for base in type(error).__mro__} if error else set()
    if error_types & {"NetworkError", "TimedOut", "RetryAfter"}:
        logger.warning(
            "Transient Telegram network error; polling will retry: %s", error
        )
        return
    logger.error(
        "Unhandled Telegram update error: %s", error, exc_info=error if error else True
    )


def _persona_startup_message() -> str:
    """Choose an awake message belonging to the active personality."""
    persona = getattr(_runtime.workflow, "persona", {}) or {}
    configured = persona.get("startup_messages", [])
    choices = [str(item).strip() for item in configured if str(item).strip()]
    if choices:
        variant = os.getenv("CURIE_STARTUP_VARIANT", "").strip()
        if variant.isdigit():
            return choices[int(variant) % len(choices)]
        return secrets.choice(choices)
    name = str(persona.get("name") or os.getenv("ASSISTANT_NAME") or "Assistant")
    return f"{name} is awake, online, and ready."


def _startup_notification_path() -> Path:
    configured = os.getenv("CURIE_STARTUP_NOTIFICATION_STATE", "").strip()
    return (
        Path(configured)
        if configured
        else Path.home() / ".curie" / "telegram-startup-notified"
    )


def _startup_notification_due() -> bool:
    try:
        cooldown = max(
            0, int(os.getenv("CURIE_STARTUP_NOTIFICATION_COOLDOWN_SECONDS", "0"))
        )
    except ValueError:
        cooldown = 0
    if cooldown == 0:
        return True
    path = _startup_notification_path()
    with _STARTUP_NOTIFICATION_LOCK:
        try:
            return not path.exists() or time.time() - path.stat().st_mtime >= cooldown
        except OSError:
            return True


def _mark_startup_notification_sent() -> None:
    try:
        cooldown = int(os.getenv("CURIE_STARTUP_NOTIFICATION_COOLDOWN_SECONDS", "0"))
    except ValueError:
        cooldown = 0
    if cooldown <= 0:
        return
    path = _startup_notification_path()
    try:
        with _STARTUP_NOTIFICATION_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.touch()
            path.chmod(0o600)
    except OSError as exc:
        logger.warning("Could not persist startup notification cooldown: %s", exc)


async def handle_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show IDs needed for an owner to configure MASTER_USER_ID safely."""
    user = update.effective_user
    if not user or not update.message:
        return
    internal_id = get_internal_id(user.id, user.username or f"telegram_{user.id}")
    assistant_name = str(
        getattr(_runtime.workflow, "persona", {}).get("name", "Assistant")
    )
    await update.message.reply_text(
        f"Your {assistant_name} internal ID is:\n"
        f"`{internal_id}`\n\nSet this as `MASTER_USER_ID` in `.env`, then restart Curie.",
        parse_mode="Markdown",
    )


async def notify_master_awake(application) -> None:
    """Send one readiness notification after Telegram initialization succeeds."""
    _runtime.loop = asyncio.get_running_loop()
    set_commands = getattr(application.bot, "set_my_commands", None)
    if set_commands:
        await set_commands(
            [
                BotCommand("start", "Start Curie and show the welcome message"),
                BotCommand("whoami", "Show your linked Curie identity"),
                BotCommand(
                    "identify", "Link this Telegram chat to your Curie identity"
                ),
                BotCommand("remember", "Save an explicit personal preference or fact"),
                BotCommand("reset", "Reset this conversation"),
                BotCommand("history", "Show conversation history statistics"),
                BotCommand("reminders", "List upcoming reminders"),
                BotCommand("poll", "Create a poll: question | option | option"),
                BotCommand("busy", "Pause proactive messages temporarily"),
                BotCommand("resume", "Resume proactive messages"),
                BotCommand("voice", "Voice replies: on, off, or status"),
                BotCommand(
                    "voice_profile", "Choose clear, soft, expressive, French, or custom"
                ),
                BotCommand("voice_accent", "Choose neutral, subtle, or strong accent"),
                BotCommand("voice_speed", "Choose slow, normal, or fast speech"),
                BotCommand("voice_warmth", "Choose neutral, gentle, or warm delivery"),
                BotCommand(
                    "voice_expression", "Choose calm, balanced, or expressive delivery"
                ),
                BotCommand("voice_sample", "Hear a sample of the current voice"),
                BotCommand(
                    "voice_custom", "Custom voice consent, enrollment, and status"
                ),
                BotCommand("voice_help", "Show all voice controls"),
                BotCommand("agenda", "Show your upcoming personal agenda"),
                BotCommand("birthday", "Add an explicitly supplied birthday"),
                BotCommand("task", "List or manage durable multi-step tasks"),
                BotCommand("audit", "Inspect or export your private action audit"),
                BotCommand("privacy", "View retention or purge expired records"),
                BotCommand("security", "Show Curie’s active security controls"),
                BotCommand("health", "Show independent capability readiness"),
                BotCommand("capabilities", "Show Curie’s healthy capabilities"),
                BotCommand("proactive", "Control proactive suggestions and quiet time"),
                BotCommand("adaptation", "Inspect or tune Curie’s learned preferences"),
                BotCommand("clear_memory", "Erase Curie’s saved memory for you"),
            ]
        )
    if os.getenv("NOTIFY_MASTER_ON_STARTUP", "true").lower() not in {
        "1",
        "true",
        "yes",
    }:
        return
    master_id = os.getenv("MASTER_USER_ID", "").strip()
    if not master_id:
        logger.info("Startup notification skipped: MASTER_USER_ID is not configured")
        return
    if not _startup_notification_due():
        logger.info("Startup notification skipped during the restart cooldown")
        return
    external_id = UserManager.get_external_id(master_id, "telegram")
    if not external_id:
        logger.warning(
            "Startup notification skipped: master %s has no stored Telegram mapping",
            master_id,
        )
        return
    try:
        await application.bot.send_message(
            chat_id=int(external_id),
            text=_persona_startup_message(),
        )
    except Exception as exc:
        # Telegram forbids a bot from initiating the first private chat. Keep
        # polling alive and tell the operator exactly how to establish consent.
        logger.warning(
            "Could not notify the master at startup: %s. The master must send "
            "/start to this bot once before it can initiate messages.",
            exc,
        )
        return
    _mark_startup_notification_sent()
    logger.info("Sent startup-ready notification to the configured master user")


def split_telegram_message(
    text: str, limit: int = 3500, preferred_limit: int = 1400
) -> list[str]:
    """Split long replies at natural boundaries below Telegram's hard limit."""
    text = (text or "").strip()
    if not text:
        return [""]
    chunks = []
    remaining = text
    target_limit = min(limit, max(400, preferred_limit))
    while len(remaining) > target_limit:
        window = remaining[: target_limit + 1]
        split_at = max(
            window.rfind("\n\n"),
            window.rfind("\n"),
            window.rfind(". "),
            window.rfind("? "),
            window.rfind("! "),
            window.rfind(" "),
        )
        if split_at < target_limit // 2:
            split_at = target_limit
        elif window[split_at : split_at + 2] in {". ", "? ", "! "}:
            split_at += 1
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def reply_in_chunks(
    message, text: str, parse_mode: Optional[str] = "HTML"
) -> None:
    """Send readable rich-text chunks and safely fall back to plain text."""
    for raw_chunk in split_telegram_message(text):
        chunk = telegram_html(raw_chunk) if parse_mode == "HTML" else raw_chunk
        try:
            await message.reply_text(chunk, parse_mode=parse_mode)
        except Exception:
            if not parse_mode:
                raise
            logger.debug("Telegram formatting failed; retrying chunk as plain text")
            await message.reply_text(strip_markdown(raw_chunk))


async def reply_with_result(message, result: dict, fallback: str) -> None:
    """Deliver deliberately separated response parts as distinct messages."""
    parts = [
        str(part).strip()
        for part in result.get("message_parts", [])
        if str(part).strip()
    ]
    if len(parts) > 1:
        for part in parts:
            await reply_in_chunks(message, part)
        return
    await reply_in_chunks(message, result.get("text", fallback))


async def _typing_heartbeat(message) -> None:
    """Keep Telegram's typing indicator alive during slower local inference."""
    try:
        while True:
            await message.get_bot().send_chat_action(
                chat_id=message.chat_id, action=_TYPING_ACTION
            )
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("Could not refresh Telegram typing indicator: %s", exc)


async def handle_busy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    set_busy_temporarily(tg_user_id)
    await update.message.reply_text(
        "D'accord! I'll let you focus for a while. I'll check in again later, mon ami."
    )


async def handle_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    clear_user_busy(tg_user_id)
    await update.message.reply_text("Bienvenue! I'm here and ready to chat again. 😊")


async def handle_voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route /voice through the shared preference service and always answer in text."""
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    internal_id = get_internal_id(user.id, user.username or f"telegram_{user.id}")
    argument = " ".join(getattr(context, "args", []) or []).strip()
    command = f"/voice {argument}".strip()
    from memory.adaptation import handle_adaptation_command
    from services.voice_commands import voice_status

    response = (
        voice_status(internal_id, "telegram")
        if argument.casefold() == "status"
        else handle_adaptation_command(internal_id, command, "telegram")
    )
    await update.message.reply_text(
        response or "Use /voice on, /voice off, or /voice status."
    )


async def handle_voice_setting_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Persist one validated voice profile control."""
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    internal_id = get_internal_id(user.id, user.username or f"telegram_{user.id}")
    command = update.message.text.split()[0].split("@")[0].removeprefix("/voice_")
    value = " ".join(getattr(context, "args", []) or []).strip()
    from services.voice_commands import configure_voice

    try:
        response = configure_voice(internal_id, command, value)
    except (KeyError, ValueError) as exc:
        response = str(exc)
    await update.message.reply_text(response)


async def handle_voice_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from services.voice_commands import VOICE_HELP

    await update.message.reply_text(VOICE_HELP)


async def handle_voice_sample(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Synthesize a one-shot sample without changing reply modality."""
    if not update.message or not update.effective_user or not _runtime.workflow:
        return
    user = update.effective_user
    internal_id = get_internal_id(user.id, user.username or f"telegram_{user.id}")
    from services.voice_commands import VOICE_SAMPLE_TEXT
    from services.voice_delivery import synthesize_reply

    sample_text = (
        " ".join(getattr(context, "args", []) or []).strip() or VOICE_SAMPLE_TEXT
    )
    sample_text = sample_text[:500]
    path = await synthesize_reply(sample_text, _runtime.workflow.persona, internal_id)
    if not path:
        await update.message.reply_text(
            "The selected voice is not ready. Use /voice status for details."
        )
        return
    try:
        with open(path, "rb") as audio:
            await update.message.reply_voice(voice=audio, caption="Curie voice sample")
    finally:
        Path(path).unlink(missing_ok=True)


async def handle_voice_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage explicit consent and owner-scoped reference enrollment."""
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    internal_id = get_internal_id(user.id, user.username or f"telegram_{user.id}")
    action = " ".join(getattr(context, "args", []) or []).strip().casefold() or "status"
    from services.voice_commands import custom_voice_command

    if action != "enroll":
        await update.message.reply_text(custom_voice_command(internal_id, action))
        return
    from memory.adaptation import get_preferences, set_custom_voice_reference

    preferences = get_preferences(internal_id)
    if not preferences.get("custom_voice_consent"):
        await update.message.reply_text("Use /voice_custom consent before enrollment.")
        return
    replied = getattr(update.message, "reply_to_message", None)
    voice = getattr(replied, "voice", None) if replied else None
    if not voice:
        await update.message.reply_text(
            "Reply to a consenting speaker’s voice note with /voice_custom enroll."
        )
        return
    owner_hash = hashlib.sha256(str(internal_id).encode()).hexdigest()[:16]
    directory = Path("models/voices/custom") / owner_hash
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = directory / "reference.ogg"
    target = directory / "reference.wav"
    telegram_file = await voice.get_file()
    await telegram_file.download_to_drive(str(source))
    from utils.voice import get_ffmpeg_executable

    ffmpeg = get_ffmpeg_executable()
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ar",
        "24000",
        "-ac",
        "1",
        str(target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, error = await process.communicate()
    source.unlink(missing_ok=True)
    if process.returncode or not target.is_file():
        target.unlink(missing_ok=True)
        logger.warning(
            "Custom voice enrollment conversion failed: %s",
            error.decode(errors="replace"),
        )
        await update.message.reply_text("I could not prepare that reference recording.")
        return
    target.chmod(0o600)
    set_custom_voice_reference(internal_id, str(target.resolve()))
    await update.message.reply_text(
        "Reference stored locally. Custom synthesis will become available when the local XTTS model is installed."
    )


async def handle_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Explicit remember command: /remember <key> <value>
    Example: /remember favorite_food pizza
    """
    tg_user_id = update.message.from_user.id
    args = context.args if hasattr(context, "args") else []

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /remember <key> <value>\nExample: /remember favorite_food pizza"
        )
        return

    key = args[0]
    value = " ".join(args[1:])

    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = get_internal_id(tg_user_id, telegram_username)

    UserManager.update_user_profile(internal_id, {key: value})
    await update.message.reply_text(f"✅ Remembered: {key} = {value}")


async def handle_identify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    args = context.args if hasattr(context, "args") else []
    if not args:
        await update.message.reply_text("Usage: /identify <your_secret_username>")
        return

    secret_username = args[0]
    internal_id = UserManager.get_internal_id_by_secret_username(secret_username)
    if internal_id:
        user_session_map[tg_user_id] = internal_id
        await update.message.reply_text(
            f"✅ Identity linked to secret_username `{secret_username}`."
        )
    else:
        await update.message.reply_text("❌ No user found with that secret_username.")


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset — any user can wipe their own conversation history.
    Routes through process_message so the logic is in one place and works
    identically across all connectors (Discord, API, etc.).
    """
    if not _runtime.workflow:
        await update.message.reply_text("❌ System not initialized.")
        return

    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = get_internal_id(tg_user_id, telegram_username)

    normalized_input = {
        "platform": "telegram",
        "external_user_id": tg_user_id,
        "external_chat_id": update.message.chat_id,
        "message_id": str(update.message.message_id),
        "text": "/reset",
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }
    result = await _runtime.workflow.process_message(normalized_input)
    await update.message.reply_text(result.get("text", "✅ Session reset."))


async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /history — show how many messages are stored for this user.
    """
    if not _runtime.workflow:
        await update.message.reply_text("❌ System not initialized.")
        return

    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = get_internal_id(tg_user_id, telegram_username)

    normalized_input = {
        "platform": "telegram",
        "external_user_id": tg_user_id,
        "external_chat_id": update.message.chat_id,
        "message_id": str(update.message.message_id),
        "text": "/history",
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }
    result = await _runtime.workflow.process_message(normalized_input)
    await update.message.reply_text(result.get("text", "📊 Could not retrieve stats."))


async def handle_clear_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = get_internal_id(tg_user_id, telegram_username)

    if not is_master_user(internal_id):
        await update.message.reply_text(
            "❌ You are not authorized to use this command."
        )
        return

    sm = get_session_manager()
    args = context.args if hasattr(context, "args") else []
    if args and args[0] == "all":
        sm.clear_all_sessions()
        await update.message.reply_text("🧹 All conversational memory cleared.")
    else:
        sm.reset_user_all_channels(internal_id)
        await update.message.reply_text(
            "🧹 Your conversational memory has been cleared."
        )


async def handle_workflow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route deterministic owner controls through the shared workflow."""
    if not update.message or not update.effective_user or not _runtime.workflow:
        return
    user = update.effective_user
    internal_id = get_internal_id(user.id, user.username or f"telegram_{user.id}")
    normalized_input = {
        "platform": "telegram",
        "external_user_id": user.id,
        "external_chat_id": update.message.chat_id,
        "message_id": str(update.message.message_id),
        "text": update.message.text or "",
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }
    result = await _runtime.workflow.process_message(normalized_input)
    await reply_with_result(update.message, result, "Command unavailable.")


async def handle_voice_message(update: Update, persona: dict) -> Optional[str]:
    """
    Handle voice message from Telegram.
    """
    from utils.voice import transcribe_audio, get_voice_config_from_persona

    voice = update.message.voice
    audio_path = f"/tmp/telegram_voice_{voice.file_id}.ogg"

    try:
        voice_file = await voice.get_file()
        await voice_file.download_to_drive(audio_path)

        logger.info(f"Downloaded voice message: {audio_path}")

        voice_config = get_voice_config_from_persona(persona)
        accent = voice_config.get("accent")
        language = voice_config.get("language", "en")

        transcribed_text = await transcribe_audio(
            audio_path,
            language=language,
            accent=accent,
            auto_detect=True,
        )

        return transcribed_text
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")
        return None
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


def _take_pending_attachment(chat_id: int) -> Optional[PendingAttachment]:
    with _pending_lock:
        attachment = _pending_attachments.pop(chat_id, None)
    if attachment and time.time() - attachment.created_at > _ATTACHMENT_TTL_SECONDS:
        Path(attachment.path).unlink(missing_ok=True)
        return None
    return attachment


def _store_pending_attachment(chat_id: int, attachment: PendingAttachment) -> None:
    previous = _take_pending_attachment(chat_id)
    if previous:
        Path(previous.path).unlink(missing_ok=True)
    with _pending_lock:
        _pending_attachments[chat_id] = attachment


async def _process_and_reply(
    update: Update, user_message: str, internal_id: str
) -> None:
    normalized_input = {
        "platform": "telegram",
        "external_user_id": update.message.from_user.id,
        "external_chat_id": update.message.chat_id,
        "message_id": update.message.message_id,
        "text": user_message,
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }
    typing_task = asyncio.create_task(_typing_heartbeat(update.message))
    try:
        result = await _runtime.workflow.process_message(normalized_input)
    finally:
        typing_task.cancel()
        with suppress(asyncio.CancelledError):
            await typing_task
    response_text = result.get("text", "[Error: No response]")
    try:
        from services.voice_delivery import synthesize_reply, voice_replies_enabled

        if voice_replies_enabled(internal_id, "telegram"):
            voice_path = await synthesize_reply(
                response_text, _runtime.workflow.persona, internal_id
            )
            if voice_path:
                try:
                    with open(voice_path, "rb") as audio:
                        if voice_path.endswith(".ogg"):
                            await update.message.reply_voice(voice=audio)
                        else:
                            await update.message.reply_audio(audio=audio)
                    return
                finally:
                    Path(voice_path).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Telegram voice reply failed; sending text: %s", exc)
    await reply_with_result(update.message, result, "[Error: No response]")


async def handle_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download a bounded photo or document and process it locally."""
    if not _runtime.workflow or not update.message:
        return
    message = update.message
    await message.reply_text("I’m checking that attachment now, mon ami.")
    media = message.photo[-1] if message.photo else message.document
    file_size = int(getattr(media, "file_size", 0) or 0)
    max_bytes = int(os.getenv("TELEGRAM_MAX_ATTACHMENT_BYTES", str(20 * 1024 * 1024)))
    if file_size > max_bytes:
        await message.reply_text(
            "That attachment is too large. The current limit is 20 MB."
        )
        return
    from services.media_ingestion import classify_attachment

    content_type = (
        getattr(media, "mime_type", None) or getattr(media, "content_type", None) or ""
    )
    filename = (
        getattr(media, "file_name", None)
        or f"telegram_{getattr(media, 'file_unique_id', media.file_id)}.jpg"
    )
    kind = "image" if message.photo else classify_attachment(filename, content_type)
    suffix = Path(filename).suffix or (".jpg" if kind == "image" else ".bin")
    fd, path = tempfile.mkstemp(prefix="curie_attachment_", suffix=suffix)
    os.close(fd)
    try:
        telegram_file = await media.get_file()
        await telegram_file.download_to_drive(path)
        attachment = PendingAttachment(path, kind, filename, time.time())
        caption = (message.caption or "").strip()
        internal_id = get_internal_id(
            message.from_user.id,
            message.from_user.username or f"telegram_{message.from_user.id}",
        )
        from services.media_ingestion import prepare_attachment_message

        user_message = await prepare_attachment_message(
            path,
            filename,
            caption,
            content_type=content_type,
            persona=_runtime.workflow.persona,
        )
        await _process_and_reply(update, user_message, internal_id)
    except Exception as exc:
        logger.warning("Attachment processing failed: %s", exc)
        await message.reply_text(str(exc))
    finally:
        Path(path).unlink(missing_ok=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler - normalize and process through ChatWorkflow."""
    if not _runtime.workflow:
        await update.message.reply_text("❌ System not initialized.")
        return

    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"

    internal_id = get_internal_id(tg_user_id, telegram_username)

    if update.message.voice:
        user_message = await handle_voice_message(update, _runtime.workflow.persona)
        if not user_message:
            await update.message.reply_text(
                "❌ Sorry, I couldn't understand the voice message."
            )
            return
        await update.message.reply_text(f"🎤 I heard: {user_message}")
    else:
        user_message = update.message.text

    # A text reply to an earlier photo/document explicitly refers to that
    # attachment. Re-download it for this turn instead of asking the model to
    # infer what “it” means from text-only history.
    replied = getattr(update.message, "reply_to_message", None)
    replied_media = None
    replied_is_photo = False
    if replied:
        replied_photos = getattr(replied, "photo", None) or []
        replied_media = (
            replied_photos[-1] if replied_photos else getattr(replied, "document", None)
        )
        replied_is_photo = bool(replied_photos)
    if replied_media and user_message:
        file_size = int(getattr(replied_media, "file_size", 0) or 0)
        max_bytes = int(
            os.getenv("TELEGRAM_MAX_ATTACHMENT_BYTES", str(20 * 1024 * 1024))
        )
        if file_size > max_bytes:
            await update.message.reply_text(
                "That referenced attachment is larger than 20 MB."
            )
            return
        filename = (
            getattr(replied_media, "file_name", None)
            or f"telegram_reply_{getattr(replied_media, 'file_unique_id', replied_media.file_id)}.jpg"
        )
        content_type = getattr(replied_media, "mime_type", None) or (
            "image/jpeg" if replied_is_photo else ""
        )
        suffix = Path(filename).suffix or (".jpg" if replied_is_photo else ".bin")
        fd, replied_path = tempfile.mkstemp(
            prefix="curie_replied_attachment_", suffix=suffix
        )
        os.close(fd)
        try:
            telegram_file = await replied_media.get_file()
            await telegram_file.download_to_drive(replied_path)
            from services.media_ingestion import prepare_attachment_message

            user_message = await prepare_attachment_message(
                replied_path,
                filename,
                user_message,
                content_type=content_type,
                persona=_runtime.workflow.persona,
            )
        except Exception as exc:
            logger.warning("Referenced attachment processing failed: %s", exc)
            await update.message.reply_text(str(exc))
            return
        finally:
            Path(replied_path).unlink(missing_ok=True)

    pending = _take_pending_attachment(update.message.chat_id)
    if pending:
        try:
            from services.media_ingestion import prepare_attachment_message

            user_message = await prepare_attachment_message(
                pending.path,
                pending.filename,
                user_message,
                persona=_runtime.workflow.persona,
            )
        except Exception as exc:
            logger.warning("Pending attachment processing failed: %s", exc)
            await update.message.reply_text(str(exc))
            return
        finally:
            Path(pending.path).unlink(missing_ok=True)

    await _process_and_reply(update, user_message, internal_id)


async def handle_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reminders — list the user's upcoming reminders."""
    if not _runtime.workflow:
        await update.message.reply_text("❌ System not initialized.")
        return

    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = get_internal_id(tg_user_id, telegram_username)

    normalized_input = {
        "platform": "telegram",
        "external_user_id": tg_user_id,
        "external_chat_id": update.message.chat_id,
        "message_id": str(update.message.message_id),
        "text": "list my reminders",
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }
    result = await _runtime.workflow.process_message(normalized_input)
    await reply_with_result(update.message, result, "📅 No reminders found.")


def start_telegram_bot(workflow: ChatWorkflow):
    """Start Telegram bot with shared ChatWorkflow."""
    _runtime.workflow = workflow

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        raise RuntimeError(
            "Telegram bot token not found in .env file or environment variables."
        )

    app = (
        ApplicationBuilder()
        .token(telegram_token)
        .post_init(notify_master_awake)
        .build()
    )
    # Store the application so send_message() can use it for proactive delivery
    _runtime.application = app

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("whoami", handle_whoami))
    app.add_handler(CommandHandler("identify", handle_identify))
    app.add_handler(CommandHandler("busy", handle_busy))
    app.add_handler(CommandHandler("resume", handle_resume))
    app.add_handler(CommandHandler("voice", handle_voice_command))
    for voice_setting in (
        "voice_profile",
        "voice_accent",
        "voice_speed",
        "voice_warmth",
        "voice_expression",
    ):
        app.add_handler(CommandHandler(voice_setting, handle_voice_setting_command))
    app.add_handler(CommandHandler("voice_sample", handle_voice_sample))
    app.add_handler(CommandHandler("voice_custom", handle_voice_custom))
    app.add_handler(CommandHandler("voice_help", handle_voice_help))
    app.add_handler(CommandHandler("remember", handle_remember))
    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(CommandHandler("history", handle_history))
    app.add_handler(CommandHandler("reminders", handle_reminders))
    app.add_handler(CommandHandler("poll", handle_poll))
    app.add_handler(CommandHandler("clear_memory", handle_clear_memory))
    for workflow_command in WORKFLOW_COMMANDS:
        app.add_handler(CommandHandler(workflow_command, handle_workflow_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_message))
    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, handle_media_message)
    )
    app.add_error_handler(handle_telegram_error)

    print("🤖 Telegram bot is running...")
    # main.py may run this connector in a worker thread alongside proactive
    # delivery. Signal handlers can only be installed from Python's main thread.
    app.run_polling(drop_pending_updates=True, stop_signals=None)
