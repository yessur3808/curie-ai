# connectors/whatsapp.py
"""
WhatsApp connector - transport-only concerns.
Receives WhatsApp events, normalizes to standard format, calls ChatWorkflow.
Supports text messages, voice messages, and media.
"""

import datetime
import os
import logging
from typing import Optional
from dotenv import load_dotenv

try:
    from whatsapp import WhatsApp
except ImportError:
    WhatsApp = None

from agent.chat_workflow import ChatWorkflow
from utils.session import set_busy_temporarily, clear_user_busy
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared ChatWorkflow instance (initialized in main.py)
_workflow = None
user_session_map = {}


def set_workflow(workflow: ChatWorkflow):
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def get_internal_id(wa_user_id: str, wa_username: str, platform: str = 'whatsapp') -> str:
    """
    Get internal user ID, respecting /identify command if used.
    
    Args:
        wa_user_id: WhatsApp user ID (phone number)
        wa_username: WhatsApp username (or fallback)
        platform: Platform name (default: 'whatsapp')
    
    Returns:
        Internal user ID (UUID string)
    """
    # Check if user has identified themselves
    if wa_user_id in user_session_map:
        return user_session_map[wa_user_id]
    
    # Fall back to standard lookup/creation
    return UserManager.get_or_create_user_internal_id(
        channel=platform,
        external_id=wa_user_id,
        secret_username=wa_username,
        updated_by='whatsapp_bot'
    )


async def handle_voice_message(message) -> Optional[str]:
    """
    Handle voice message from WhatsApp.
    Downloads the audio and converts it to text using speech recognition.
    
    Args:
        message: WhatsApp message object with voice/audio
        
    Returns:
        Transcribed text or None if transcription fails
    """
    audio_file = None
    try:
        # Import voice utilities
        from utils.voice import transcribe_audio, get_voice_config_from_persona
        
        # Download voice message
        audio_file = await message.download_media()
        if not audio_file:
            logger.error("Failed to download voice message")
            return None
        
        # Get voice config from persona for accent-aware transcription
        persona = _workflow.persona if _workflow else None
        voice_config = get_voice_config_from_persona(persona) if persona else {}
        accent = voice_config.get('accent')
        language = voice_config.get('language', 'en')
        
        # Transcribe audio to text with accent awareness
        transcribed_text = await transcribe_audio(
            audio_file,
            language=language,
            accent=accent,
            auto_detect=True
        )
        
        return transcribed_text
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")
        return None
    finally:
        # Clean up temporary file regardless of success or failure
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)


async def handle_message(message):
    """Main message handler - normalize and process through ChatWorkflow."""
    if not _workflow:
        await message.reply("❌ System not initialized.")
        return
    
    try:
        # Extract message details
        wa_user_id = message.from_user.phone
        wa_username = message.from_user.name or f"whatsapp_{wa_user_id}"
        message_id = message.id
        chat_id = message.chat.id
        
        # Get internal ID (respects /identify if used)
        internal_id = get_internal_id(wa_user_id, wa_username)
        
        # Handle voice messages
        if message.voice or message.audio:
            user_message = await handle_voice_message(message)
            if not user_message:
                await message.reply("❌ Sorry, I couldn't understand the voice message.")
                return
            await message.reply(f"🎤 I heard: {user_message}")
        else:
            user_message = message.text or ""
        
        # Handle special commands
        if user_message.startswith('/start'):
            greeting = _workflow.persona.get("greeting", "Hello!")
            await message.reply(f"{greeting}")
            return
        
        if user_message.startswith('/busy'):
            set_busy_temporarily(wa_user_id)
            await message.reply(
                "D'accord! I'll let you focus for a while. I'll check in again later, mon ami."
            )
            return
        
        if user_message.startswith('/resume'):
            clear_user_busy(wa_user_id)
            await message.reply(
                "Bienvenue! I'm here and ready to chat again. 😊"
            )
            return
        
        if user_message.startswith('/identify'):
            parts = user_message.split(maxsplit=1)
            if len(parts) < 2:
                await message.reply("Usage: /identify <your_secret_username>")
                return
            
            secret_username = parts[1]
            internal_id_found = UserManager.get_internal_id_by_secret_username(secret_username)
            if internal_id_found:
                user_session_map[wa_user_id] = internal_id_found
                await message.reply(f"✅ Identity linked to secret_username `{secret_username}`.")
            else:
                await message.reply("❌ No user found with that secret_username.")
            return
        
        # Normalize to standard ChatWorkflow format
        normalized_input = {
            'platform': 'whatsapp',
            'external_user_id': wa_user_id,
            'external_chat_id': chat_id,
            'message_id': message_id,
            'text': user_message,
            'timestamp': datetime.datetime.utcnow(),
            'internal_id': internal_id
        }
        
        # Process through workflow
        result = await _workflow.process_message(normalized_input)
        
        # Send response
        response_text = result.get('text', '[Error: No response]')
        await message.reply(response_text)
        
    except Exception as e:
        logger.error(f"Error handling WhatsApp message: {e}")
        await message.reply("❌ Sorry, an error occurred processing your message.")


def start_whatsapp_bot(workflow: ChatWorkflow):
    """Start WhatsApp bot with shared ChatWorkflow."""
    global _workflow
    _workflow = workflow
    
    if WhatsApp is None:
        raise RuntimeError(
            "whatsapp-web.py is not installed. Install it with: pip install whatsapp-web.py"
        )
    
    # Note: WhatsApp Web requires QR code authentication
    # The session will be saved for future use
    logger.info("🟢 Starting WhatsApp bot...")
    logger.info("📱 Please scan the QR code with your WhatsApp mobile app")
    
    try:
        # Initialize WhatsApp client
        client = WhatsApp()
        
        # Register message handler
        @client.on_message()
        async def on_message(message):
            await handle_message(message)
        
        # Start the client
        logger.info("🤖 WhatsApp bot is running...")
        client.run()
        
    except Exception as e:
        logger.error(f"Failed to start WhatsApp bot: {e}")
        raise RuntimeError(f"WhatsApp bot initialization failed: {e}")
