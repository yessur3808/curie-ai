# connectors/telegram.py
"""
Telegram connector - transport-only concerns.
Receives Telegram events, normalizes to standard format, calls ChatWorkflow.
"""

import datetime
import os
import logging
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from agent.chat_workflow import ChatWorkflow

from utils.persona import load_persona
from utils.session import set_busy_temporarily, clear_user_busy
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared ChatWorkflow instance (initialized in main.py)
_workflow = None
user_persona_map = {}
user_session_map = {}


def set_workflow(workflow: ChatWorkflow):
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def get_internal_id(tg_user_id: int, telegram_username: str, platform: str = 'telegram') -> str:
    """
    Get internal user ID, respecting /identify command if used.
    
    If the user has identified themselves via /identify command, use that internal_id.
    Otherwise, fall back to get_or_create_user_internal_id.
    
    Args:
        tg_user_id: Telegram user ID
        telegram_username: Telegram username (or fallback)
        platform: Platform name (default: 'telegram')
    
    Returns:
        Internal user ID (UUID string)
    """
    # Check if user has identified themselves
    if tg_user_id in user_session_map:
        return user_session_map[tg_user_id]
    
    # Fall back to standard lookup/creation
    return UserManager.get_or_create_user_internal_id(
        channel=platform,
        external_id=tg_user_id,
        secret_username=telegram_username,
        updated_by='telegram_bot'
    )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _workflow:
        await update.message.reply_text("❌ System not initialized.")
        return
    
    greeting = _workflow.persona.get("greeting", "Hello!")
    await update.message.reply_text(f"{greeting}")


async def handle_busy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    set_busy_temporarily(tg_user_id)
    await update.message.reply_text(
        "D'accord! I'll let you focus for a while. I'll check in again later, mon ami."
    )


async def handle_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    clear_user_busy(tg_user_id)
    await update.message.reply_text(
        "Bienvenue! I'm here and ready to chat again. 😊"
    )


async def handle_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Explicit remember command: /remember <key> <value>
    Example: /remember favorite_food pizza
    """
    tg_user_id = update.message.from_user.id
    args = context.args if hasattr(context, 'args') else []
    
    if len(args) < 2:
        await update.message.reply_text("Usage: /remember <key> <value>\nExample: /remember favorite_food pizza")
        return
    
    key = args[0]
    value = " ".join(args[1:])
    
    # Get internal ID (respects /identify)
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = get_internal_id(tg_user_id, telegram_username)
    
    # Save fact
    UserManager.update_user_profile(internal_id, {key: value})
    await update.message.reply_text(f"✅ Remembered: {key} = {value}")


async def handle_identify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    args = context.args if hasattr(context, 'args') else []
    if not args:
        await update.message.reply_text("Usage: /identify <your_secret_username>")
        return

    secret_username = args[0]
    internal_id = UserManager.get_internal_id_by_secret_username(secret_username)
    if internal_id:
        user_session_map[tg_user_id] = internal_id
        await update.message.reply_text(f"✅ Identity linked to secret_username `{secret_username}`.")
    else:
        await update.message.reply_text("❌ No user found with that secret_username.")


async def handle_clear_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from memory import ConversationManager
    from utils.db import is_master_user
    
    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = get_internal_id(tg_user_id, telegram_username)

    if not is_master_user(internal_id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    args = context.args if hasattr(context, 'args') else []
    if args and args[0] == "all":
        ConversationManager.clear_conversation()
        await update.message.reply_text("🧹 All conversational memory cleared.")
    else:
        ConversationManager.clear_conversation(internal_id)
        await update.message.reply_text("🧹 Your conversational memory has been cleared.")


async def handle_voice_message(update: Update, persona: dict) -> str:
    """
    Handle voice message from Telegram.
    Downloads the audio and converts it to text using speech recognition.
    Uses persona voice config for accent-aware recognition.
    
    Args:
        update: Telegram update object with voice message
        persona: Persona dictionary with voice configuration
        
    Returns:
        Transcribed text or empty string if transcription fails
    """
    # Import voice utilities
    from utils.voice import transcribe_audio, get_voice_config_from_persona

    # Prepare audio path for temporary file
    voice = update.message.voice
    audio_path = f"/tmp/telegram_voice_{voice.file_id}.ogg"

    try:
        # Get voice file and download to temporary location
        voice_file = await voice.get_file()
        await voice_file.download_to_drive(audio_path)

        logger.info(f"Downloaded voice message: {audio_path}")

        # Get voice config from persona
        voice_config = get_voice_config_from_persona(persona)
        accent = voice_config.get('accent')
        language = voice_config.get('language', 'en')

        # Transcribe audio to text with accent awareness
        transcribed_text = await transcribe_audio(
            audio_path,
            language=language,
            accent=accent,
            auto_detect=True,
        )

        return transcribed_text or ""
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")
        return ""
    finally:
        # Clean up temporary file regardless of success or failure
        if os.path.exists(audio_path):
            os.remove(audio_path)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler - normalize and process through ChatWorkflow."""
    if not _workflow:
        await update.message.reply_text("❌ System not initialized.")
        return
    
    tg_user_id = update.message.from_user.id
    message_id = update.message.message_id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    
    # Get internal ID (respects /identify if used)
    internal_id = get_internal_id(tg_user_id, telegram_username)
    
    # Handle voice messages with persona-aware recognition
    if update.message.voice:
        user_message = await handle_voice_message(update, _workflow.persona)
        if not user_message:
            await update.message.reply_text("❌ Sorry, I couldn't understand the voice message.")
            return
        await update.message.reply_text(f"🎤 I heard: {user_message}")
    else:
        user_message = update.message.text
    
    # Normalize to standard ChatWorkflow format
    normalized_input = {
        'platform': 'telegram',
        'external_user_id': tg_user_id,
        'external_chat_id': update.message.chat_id,
        'message_id': message_id,
        'text': user_message,
        'timestamp': datetime.datetime.utcnow(),
        'internal_id': internal_id  # Pass identified internal_id to workflow
    }
    
    # Process through workflow
    result = await _workflow.process_message(normalized_input)
    
    # Send response
    response_text = result.get('text', '[Error: No response]')
    await update.message.reply_text(response_text)


def start_telegram_bot(workflow: ChatWorkflow):
    """Start Telegram bot with shared ChatWorkflow."""
    global _workflow
    _workflow = workflow
    
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not telegram_token:
        raise RuntimeError("Telegram bot token not found in .env file or environment variables.")

    app = ApplicationBuilder().token(telegram_token).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("identify", handle_identify))
    app.add_handler(CommandHandler("busy", handle_busy))
    app.add_handler(CommandHandler("resume", handle_resume))
    app.add_handler(CommandHandler("remember", handle_remember))
    app.add_handler(CommandHandler("clear_memory", handle_clear_memory))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_message))  # Handle voice messages

    print("🤖 Telegram bot is running...")
    app.run_polling(drop_pending_updates=True)