# connectors/discord_bot.py
"""
Discord connector - transport-only concerns.
Receives Discord events, normalizes to standard format, calls ChatWorkflow.
Supports text messages, voice channels, and DMs.
"""

import asyncio
import datetime
import os
import logging
from typing import Optional
from dotenv import load_dotenv

try:
    import discord
    from discord.ext import commands
except ImportError:
    discord = None
    commands = None

from agent.chat_workflow import ChatWorkflow
from utils.persona import load_persona
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


def get_internal_id(discord_user_id: int, discord_username: str, platform: str = 'discord') -> str:
    """
    Get internal user ID, respecting /identify command if used.
    
    Args:
        discord_user_id: Discord user ID
        discord_username: Discord username
        platform: Platform name (default: 'discord')
    
    Returns:
        Internal user ID (UUID string)
    """
    # Check if user has identified themselves
    if discord_user_id in user_session_map:
        return user_session_map[discord_user_id]
    
    # Fall back to standard lookup/creation
    return UserManager.get_or_create_user_internal_id(
        channel=platform,
        external_id=str(discord_user_id),
        secret_username=discord_username,
        updated_by='discord_bot'
    )


async def handle_voice_attachment(attachment) -> Optional[str]:
    """
    Handle voice/audio attachment from Discord.
    Downloads the audio and converts it to text using speech recognition.
    
    Args:
        attachment: Discord attachment object
        
    Returns:
        Transcribed text or None if transcription fails
    """
    try:
        # Import voice utilities
        from utils.voice import transcribe_audio
        
        # Download audio file
        audio_path = f"/tmp/discord_audio_{attachment.id}.{attachment.filename.split('.')[-1]}"
        await attachment.save(audio_path)
        
        # Transcribe audio to text
        transcribed_text = await transcribe_audio(audio_path)
        
        # Clean up temporary file
        if os.path.exists(audio_path):
            os.remove(audio_path)
        
        return transcribed_text
    except Exception as e:
        logger.error(f"Error processing voice attachment: {e}")
        return None


class DiscordBot(commands.Bot):
    """Discord bot class with custom event handlers."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.workflow = None
    
    async def on_ready(self):
        """Called when the bot is ready."""
        logger.info(f'🤖 Discord bot logged in as {self.user.name} (ID: {self.user.id})')
        logger.info(f'📊 Connected to {len(self.guilds)} guilds')
    
    async def on_message(self, message):
        """Handle incoming messages."""
        # Ignore messages from the bot itself
        if message.author == self.user:
            return
        
        # Ignore messages from other bots (optional)
        if message.author.bot:
            return
        
        # Process commands first
        await self.process_commands(message)
        
        # Only process non-command messages
        if not message.content.startswith(self.command_prefix):
            await self.handle_chat_message(message)
    
    async def handle_chat_message(self, message):
        """Process regular chat messages through ChatWorkflow."""
        if not self.workflow:
            await message.channel.send("❌ System not initialized.")
            return
        
        try:
            # Extract message details
            discord_user_id = message.author.id
            discord_username = f"{message.author.name}#{message.author.discriminator}"
            message_id = message.id
            channel_id = message.channel.id
            
            # Get internal ID
            internal_id = get_internal_id(discord_user_id, discord_username)
            
            # Handle voice/audio attachments
            user_message = message.content
            if message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and 'audio' in attachment.content_type:
                        transcribed = await handle_voice_attachment(attachment)
                        if transcribed:
                            user_message += f"\n[Voice message transcribed]: {transcribed}"
                            await message.channel.send(f"🎤 I heard: {transcribed}")
            
            if not user_message:
                return
            
            # Normalize to standard ChatWorkflow format
            normalized_input = {
                'platform': 'discord',
                'external_user_id': str(discord_user_id),
                'external_chat_id': str(channel_id),
                'message_id': str(message_id),
                'text': user_message,
                'timestamp': datetime.datetime.utcnow(),
                'internal_id': internal_id
            }
            
            # Process through workflow
            result = await self.workflow.process_message(normalized_input)
            
            # Send response (Discord has 2000 char limit)
            response_text = result.get('text', '[Error: No response]')
            
            # Split long messages
            if len(response_text) > 2000:
                chunks = [response_text[i:i+2000] for i in range(0, len(response_text), 2000)]
                for chunk in chunks:
                    await message.channel.send(chunk)
            else:
                await message.channel.send(response_text)
            
        except Exception as e:
            logger.error(f"Error handling Discord message: {e}")
            await message.channel.send("❌ Sorry, an error occurred processing your message.")


def create_discord_bot(workflow: ChatWorkflow) -> DiscordBot:
    """Create and configure Discord bot instance."""
    if discord is None or commands is None:
        raise RuntimeError(
            "discord.py is not installed. Install it with: pip install discord.py"
        )
    
    # Set up intents (required for Discord bots)
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.guilds = True
    
    # Create bot with command prefix
    bot = DiscordBot(
        command_prefix='!',
        intents=intents,
        help_command=None  # Disable default help command
    )
    bot.workflow = workflow
    
    # Register commands
    @bot.command(name='start')
    async def start_command(ctx):
        """Start command - greet the user."""
        greeting = workflow.persona.get("greeting", "Hello!")
        await ctx.send(greeting)
    
    @bot.command(name='busy')
    async def busy_command(ctx):
        """Mark user as busy."""
        set_busy_temporarily(ctx.author.id)
        await ctx.send(
            "D'accord! I'll let you focus for a while. I'll check in again later, mon ami."
        )
    
    @bot.command(name='resume')
    async def resume_command(ctx):
        """Resume chat after being busy."""
        clear_user_busy(ctx.author.id)
        await ctx.send(
            "Bienvenue! I'm here and ready to chat again. 😊"
        )
    
    @bot.command(name='identify')
    async def identify_command(ctx, secret_username: str = None):
        """Link Discord account to internal user ID."""
        if not secret_username:
            await ctx.send("Usage: !identify <your_secret_username>")
            return
        
        discord_user_id = ctx.author.id
        internal_id = UserManager.get_internal_id_by_secret_username(secret_username)
        if internal_id:
            user_session_map[discord_user_id] = internal_id
            await ctx.send(f"✅ Identity linked to secret_username `{secret_username}`.")
        else:
            await ctx.send("❌ No user found with that secret_username.")
    
    @bot.command(name='help')
    async def help_command(ctx):
        """Show available commands."""
        help_text = """
**Available Commands:**
• `!start` - Start conversation with the bot
• `!busy` - Mark yourself as busy
• `!resume` - Resume conversation after being busy
• `!identify <username>` - Link your Discord account
• `!help` - Show this help message

You can also just chat with me directly without commands!
        """
        await ctx.send(help_text)
    
    @bot.command(name='clear_memory')
    async def clear_memory_command(ctx):
        """Clear conversation memory (master users only)."""
        from memory import ConversationManager
        from utils.db import is_master_user
        
        discord_user_id = ctx.author.id
        discord_username = f"{ctx.author.name}#{ctx.author.discriminator}"
        internal_id = get_internal_id(discord_user_id, discord_username)
        
        if not is_master_user(internal_id):
            await ctx.send("❌ You are not authorized to use this command.")
            return
        
        ConversationManager.clear_conversation(internal_id)
        await ctx.send("🧹 Your conversational memory has been cleared.")
    
    return bot


def start_discord_bot(workflow: ChatWorkflow):
    """Start Discord bot with shared ChatWorkflow."""
    global _workflow
    _workflow = workflow
    
    discord_token = os.getenv('DISCORD_BOT_TOKEN')
    if not discord_token:
        raise RuntimeError("Discord bot token not found in .env file or environment variables.")
    
    logger.info("🟣 Starting Discord bot...")
    
    try:
        bot = create_discord_bot(workflow)
        bot.run(discord_token)
    except Exception as e:
        logger.error(f"Failed to start Discord bot: {e}")
        raise RuntimeError(f"Discord bot initialization failed: {e}")
