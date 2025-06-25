import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from agent.core import Agent
from utils.busy import detect_busy_intent, detect_resume_intent
from utils.persona import load_persona
from utils.session import small_talk_chance
from memory import UserManager, ConversationManager
from llm.manager import clean_assistant_reply
import random

load_dotenv()

app = FastAPI(title="Curie AI API")
persona = load_persona()
agent = Agent(persona=persona)

class MessageRequest(BaseModel):
    user_id: str
    username: str = None
    message: str

class MessageResponse(BaseModel):
    response: str
    small_talk: str = None
    intent: str = None
    pr_url: str = None  # For future code/PR skills

def get_internal_id(user_id, username=None):
    # Maps external user_id to internal_id
    return agent.get_or_create_internal_id(
        external_id=user_id,
        channel='api',
        secret_username=username or f"api_{user_id}"
    )

@app.post("/chat", response_model=MessageResponse)
async def chat_api(req: MessageRequest):
    user_id = req.user_id
    username = req.username
    message = req.message

    internal_id = get_internal_id(user_id, username)

    # Busy/resume detection
    message_lc = message.lower().strip()
    if detect_busy_intent(message_lc):
        response = agent.handle_busy(internal_id)
        return MessageResponse(response=response, intent="busy")

    if detect_resume_intent(message_lc):
        response = agent.handle_resume(internal_id)
        return MessageResponse(response=response, intent="resume")

    # LLM-based intent detection fallback
    intent = agent.classify_intent_llm(message)
    if intent == "busy":
        response = agent.handle_busy(internal_id)
        return MessageResponse(response=response, intent="busy")
    elif intent == "resume":
        response = agent.handle_resume(internal_id)
        return MessageResponse(response=response, intent="resume")

    # Main conversation
    agent_response = agent.handle_message(message, internal_id=internal_id)
    agent_response = clean_assistant_reply(agent_response)

    # Small talk suggestion
    small_talk = None
    if random.random() < small_talk_chance(internal_id):
        small_talk = agent.generate_small_talk(internal_id)
        if small_talk:
            small_talk = clean_assistant_reply(small_talk)

    return MessageResponse(
        response=agent_response,
        small_talk=small_talk,
        intent="chat"
    )


@app.post("/clear_memory")
async def clear_memory_api(req: MessageRequest):
    user_id = req.user_id
    username = req.username
    internal_id = get_internal_id(user_id, username)
    ConversationManager.clear_conversation(internal_id)
    return {"status": "ok", "message": "Memory cleared."}