# connectors/telegram.py

import datetime
import random
import os
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from agent.core import Agent

from utils.busy import detect_busy_intent, detect_resume_intent
from utils.persona import load_persona
from utils.weather import get_weather, extract_city_from_message, get_hko_typhoon_signal
from utils.session import (
    set_busy_temporarily,
    is_user_busy,
    clear_user_busy,
    small_talk_chance,
)

from memory import UserManager, ConversationManager
from llm.manager import clean_assistant_reply

load_dotenv()

MASTER_USER_ID = os.getenv("MASTER_USER_ID")
user_weather_alerts = {}
user_persona_map = {}
# Maps telegram user_id to internal_id for this session
user_session_map = {}



# Small talk prompts for Curie
SMALL_TALK_QUESTIONS = [
    "By the way, what do you enjoy doing in your free time?",
    "Is there something new you've learned recently, mon ami?",
    "Do you have a favorite book or movie?",
    "What are you curious about these days?",
    "If you could travel anywhere, where would you go?",
    "C'est intéressant! Do you have any hobbies you love?",
]

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

def get_agent_for_user(update, context):
    agents = context.bot_data['agents']
    default_agent_name = context.bot_data['default_agent_name']
    user_id = update.message.from_user.id
    persona_name = user_persona_map.get(user_id, default_agent_name)
    if persona_name not in agents:
        persona_name = default_agent_name
        user_persona_map[user_id] = persona_name
    return agents[persona_name]

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent_for_user(update, context)
    greeting = agent.persona.get("greeting", "Hello!")
    agents = context.bot_data['agents']
    persona_list = "\n".join(f"- {name}" for name in agents)
    await update.message.reply_text(
        f"{greeting}\n\nYou can change my style anytime with /persona <name>.\nAvailable personas:\n{persona_list}"
    )

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    tg_user_id = update.message.from_user.id
    agent = get_agent_for_user(update, context)
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = agent.get_or_create_internal_id(
        external_id=tg_user_id,
        channel='telegram',
        secret_username=telegram_username
    )

    # --- Proactive weather heads-up (call only at right time) ---
    now = datetime.datetime.now()
    if 6 <= now.hour <= 8:
        last_alert = user_weather_alerts.get(internal_id)
        if last_alert != now.date():
            heads_up = await agent.proactive_weather_heads_up(internal_id)
            await update.message.reply_text(heads_up)
            user_weather_alerts[internal_id] = now.date()

    # --- All main business logic handled by agent.route_message ---
    handled, response = await agent.route_message(user_message, internal_id)
    if handled:
        await update.message.reply_text(response)
        return

    # --- Otherwise, normal conversation (LLM chat) ---
    agent_response = agent.handle_message(user_message, internal_id=internal_id)
    agent_response = clean_assistant_reply(agent_response)
    await update.message.reply_text(agent_response)

    if random.random() < small_talk_chance(internal_id):
        small_talk = agent.generate_small_talk(internal_id)
        if small_talk:
            small_talk = clean_assistant_reply(small_talk)
            await update.message.reply_text(small_talk)
            

async def handle_clear_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"
    internal_id = UserManager.get_or_create_user_internal_id(
        channel='telegram',
        external_id=tg_user_id,
        secret_username=telegram_username,
        updated_by='telegram_bot'
    )

    # Only allow master user
    from utils.db import is_master_user
    if not is_master_user(internal_id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    # Check for optional argument to clear all memory
    args = context.args if hasattr(context, 'args') else []
    if args and args[0] == "all":
        ConversationManager.clear_conversation()
        await update.message.reply_text("🧹 All conversational memory cleared.")
    else:
        ConversationManager.clear_conversation(internal_id)
        await update.message.reply_text("🧹 Your conversational memory has been cleared.")


async def handle_index_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent_for_user(update, context)
    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"

    internal_id = agent.get_or_create_internal_id(
        external_id=tg_user_id,
        channel='telegram',
        secret_username=telegram_username
    )

    # Allow: /indexproject [optional path]
    args = context.args if hasattr(context, 'args') else []
    path = args[0] if args else None
    try:
        agent.set_project_dir(internal_id, path)
        md = agent.get_project_markdown(internal_id)
        # Telegram message cap is 4096 chars
        await update.message.reply_text(md[:4000] if md else "Project indexed, but nothing to show.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error indexing project: {e}")

async def handle_new_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent_for_user(update, context)
    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"

    internal_id = agent.get_or_create_internal_id(
        external_id=tg_user_id,
        channel='telegram',
        secret_username=telegram_username
    )

    args = context.args if hasattr(context, 'args') else []
    if not args:
        await update.message.reply_text("Usage: /newproject <project_name>")
        return
    project_name = args[0]
    try:
        new_path, md_path = agent.create_new_project(internal_id, project_name)
        await update.message.reply_text(f"✅ Created new project at `{new_path}` with starter README.md.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error creating project: {e}")

async def handle_project_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent_for_user(update, context)
    tg_user_id = update.message.from_user.id
    telegram_username = update.message.from_user.username or f"telegram_{tg_user_id}"

    internal_id = agent.get_or_create_internal_id(
        external_id=tg_user_id,
        channel='telegram',
        secret_username=telegram_username
    )

    # Allow: /projecthelp <question>
    args = context.args if hasattr(context, 'args') else []
    if not args:
        await update.message.reply_text("Usage: /projecthelp <your question>")
        return
    question = " ".join(args)
    try:
        answer = agent.project_help(internal_id, question)
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_persona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agents = context.bot_data['agents']
    user_id = update.message.from_user.id
    args = context.args
    if not args or args[0] not in agents:
        choices = "\n".join(f"- {name}" for name in agents)
        await update.message.reply_text(
            "Usage: /persona <name>\nAvailable personas:\n" + choices
        )
        return
    persona_name = args[0]
    user_persona_map[user_id] = persona_name
    persona_desc = agents[persona_name].persona.get("description", "")
    await update.message.reply_text(f"Persona set to {persona_name}!\n\n{persona_desc}")
        

def start_telegram_bot(agents):
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not telegram_token:
        raise RuntimeError("Telegram bot token not found in .env file or environment variables.")

    app = ApplicationBuilder().token(telegram_token).build()
    
    # Handle both single-agent and multi-agent mode
    if isinstance(agents, dict):
        default_agent_name = os.getenv("DEFAULT_PERSONA_NAME") or next(iter(agents))
        default_agent = agents[default_agent_name]
        app.bot_data['agents'] = agents
        app.bot_data['default_agent_name'] = default_agent_name
        app.bot_data['default_agent'] = default_agent
        print(f"🤖 Telegram bot is running in multi-persona mode. Default: {default_agent_name}")
    else:
        # Single agent mode
        default_agent = agents
        app.bot_data['agents'] = {'default': default_agent}
        app.bot_data['default_agent_name'] = 'default'
        app.bot_data['default_agent'] = default_agent
        print(f"🤖 Telegram bot is running in single-persona mode. Current: {default_agent}")

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("persona", handle_persona))
    app.add_handler(CommandHandler("identify", handle_identify))
    app.add_handler(CommandHandler("busy", handle_busy))
    app.add_handler(CommandHandler("resume", handle_resume))
    app.add_handler(CommandHandler("clear_memory", handle_clear_memory))
    
    app.add_handler(CommandHandler("indexproject", handle_index_project))
    app.add_handler(CommandHandler("newproject", handle_new_project))
    app.add_handler(CommandHandler("projecthelp", handle_project_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Telegram bot is running...")
    app.run_polling()