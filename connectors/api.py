import os
import datetime
import uuid
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from agent.chat_workflow import ChatWorkflow

load_dotenv()

app = FastAPI(title="Curie AI API")

# Shared workflow instance (set by main.py)
_workflow = None


def set_workflow(workflow: ChatWorkflow):
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


class MessageRequest(BaseModel):
    user_id: str
    message: str
    idempotency_key: str = None  # Optional: for idempotency


class MessageResponse(BaseModel):
    text: str
    timestamp: str
    model_used: str
    processing_time_ms: float


@app.post("/chat", response_model=MessageResponse)
async def chat_api(req: MessageRequest):
    """
    Main chat endpoint - normalized interface using ChatWorkflow.
    
    Example request:
    {
        "user_id": "user123",
        "message": "Hello, how are you?",
        "idempotency_key": "optional-uuid-for-dedup"
    }
    """
    if not _workflow:
        raise HTTPException(status_code=500, detail="System not initialized")
    
    # Generate or use provided idempotency key
    message_id = req.idempotency_key or str(uuid.uuid4())
    
    # Normalize to standard ChatWorkflow format
    normalized_input = {
        'platform': 'api',
        'external_user_id': req.user_id,
        'external_chat_id': req.user_id,  # For API, use user_id as chat_id
        'message_id': message_id,
        'text': req.message,
        'timestamp': datetime.datetime.utcnow()
    }
    
    # Process through workflow
    result = await _workflow.process_message(normalized_input)
    
    return MessageResponse(
        text=result['text'],
        timestamp=result['timestamp'].isoformat(),
        model_used=result['model_used'],
        processing_time_ms=result['processing_time_ms']
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "workflow_initialized": _workflow is not None,
        "cache_stats": _workflow.get_cache_stats() if _workflow else {}
    }



@app.post("/clear_memory")
async def clear_memory_api(req: MessageRequest):
    user_id = req.user_id
    username = req.username
    internal_id = get_internal_id(user_id, username)
    ConversationManager.clear_conversation(internal_id)
    return {"status": "ok", "message": "Memory cleared."}