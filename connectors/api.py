import os
import datetime
import uuid
import logging
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agent.chat_workflow import ChatWorkflow

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Curie AI API")

# Shared workflow instance (set by main.py)
_workflow = None

# Active WebSocket connections
active_connections = []


def set_workflow(workflow: ChatWorkflow):
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


class MessageRequest(BaseModel):
    user_id: str
    message: str
    idempotency_key: str = None  # Optional: for idempotency
    voice_response: bool = False  # Optional: request voice response
    stream: bool = False  # Optional: stream response


class MessageResponse(BaseModel):
    text: str
    timestamp: str
    model_used: str
    processing_time_ms: float
    voice_url: Optional[str] = None  # Optional: URL to voice response


class VoiceTranscriptionRequest(BaseModel):
    user_id: str
    language: str = "en"
    accent: Optional[str] = None


@app.post("/chat", response_model=MessageResponse)
async def chat_api(req: MessageRequest):
    """
    Main chat endpoint - normalized interface using ChatWorkflow.
    
    Example request:
    {
        "user_id": "user123",
        "message": "Hello, how are you?",
        "idempotency_key": "optional-uuid-for-dedup",
        "voice_response": false,
        "stream": false
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
    
    # Generate voice response if requested
    voice_url = None
    if req.voice_response:
        try:
            from utils.voice import text_to_speech, get_voice_config_from_persona
            voice_config = get_voice_config_from_persona(_workflow.persona)
            
            # Generate voice file
            voice_filename = f"voice_{message_id}.mp3"
            voice_path = f"/tmp/{voice_filename}"
            
            success = await text_to_speech(result['text'], voice_path, voice_config)
            if success:
                voice_url = f"/audio/{voice_filename}"
                logger.info(f"Generated voice response: {voice_url}")
        except Exception as e:
            logger.error(f"Failed to generate voice response: {e}")
    
    return MessageResponse(
        text=result['text'],
        timestamp=result['timestamp'].isoformat(),
        model_used=result['model_used'],
        processing_time_ms=result['processing_time_ms'],
        voice_url=voice_url
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "workflow_initialized": _workflow is not None,
        "cache_stats": _workflow.get_cache_stats() if _workflow else {}
    }


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket endpoint for real-time chat.
    
    Client sends: {"user_id": "user123", "message": "Hello"}
    Server responds: {"text": "Response", "timestamp": "...", "model_used": "..."}
    """
    await websocket.accept()
    active_connections.append(websocket)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            
            if not _workflow:
                await websocket.send_json({"error": "System not initialized"})
                continue
            
            user_id = data.get('user_id')
            message = data.get('message')
            
            if not user_id or not message:
                await websocket.send_json({"error": "Missing user_id or message"})
                continue
            
            # Process message
            message_id = str(uuid.uuid4())
            normalized_input = {
                'platform': 'websocket',
                'external_user_id': user_id,
                'external_chat_id': user_id,
                'message_id': message_id,
                'text': message,
                'timestamp': datetime.datetime.utcnow()
            }
            
            result = await _workflow.process_message(normalized_input)
            
            # Send response
            await websocket.send_json({
                "text": result['text'],
                "timestamp": result['timestamp'].isoformat(),
                "model_used": result['model_used'],
                "processing_time_ms": result['processing_time_ms']
            })
            
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            # Attempt to notify client about the internal error
            await websocket.send_json({"error": "Internal server error"})
            # Ensure the WebSocket is properly closed
            await websocket.close()
        except Exception:
            # If sending the error or closing fails, just log and continue cleanup
            logger.debug("Failed to send error message or close WebSocket", exc_info=True)
        finally:
            if websocket in active_connections:
                active_connections.remove(websocket)


@app.post("/transcribe")
async def transcribe_audio_api(
    file: UploadFile = File(...),
    user_id: str = None,
    language: str = "en",
    accent: str = None
):
    """
    Transcribe audio file to text.
    
    Args:
        file: Audio file (mp3, wav, ogg, etc.)
        user_id: Optional user ID
        language: Language code (default: en)
        accent: Specific accent (optional)
        
    Returns:
        {"text": "transcribed text", "language": "detected_language"}
    """
    if not _workflow:
        raise HTTPException(status_code=500, detail="System not initialized")
    
    # Validate file extension
    ALLOWED_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.opus'}
    file_ext = os.path.splitext(file.filename)[1].lower()
    
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    try:
        from utils.voice import transcribe_audio
        
        # Save uploaded file temporarily with sanitized name
        safe_filename = f"upload_{uuid.uuid4()}{file_ext}"
        temp_path = os.path.join("/tmp", safe_filename)
        
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Transcribe
        transcribed_text = await transcribe_audio(
            temp_path,
            language=language,
            accent=accent,
            auto_detect=True
        )
        
        # Clean up
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        if not transcribed_text:
            raise HTTPException(status_code=400, detail="Failed to transcribe audio")
        
        return {
            "text": transcribed_text,
            "language": language
        }
        
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


@app.get("/audio/{filename}")
async def get_audio_file(filename: str):
    """
    Serve generated audio files.
    
    Args:
        filename: Audio filename
        
    Returns:
        Audio file as streaming response
    """
    # Sanitize filename to prevent path traversal
    # Only allow alphanumeric, dash, underscore, and dot
    import re
    if not re.match(r'^[a-zA-Z0-9_\-]+\.(mp3|wav|ogg)$', filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = os.path.join("/tmp", filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    
    # Ensure the file is actually in /tmp (prevent path traversal)
    real_path = os.path.realpath(file_path)
    if not real_path.startswith("/tmp/"):
        raise HTTPException(status_code=403, detail="Access denied")
    
    def iter_file():
        with open(file_path, "rb") as f:
            yield from f
    
    return StreamingResponse(
        iter_file(),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )



@app.post("/clear_memory")
async def clear_memory_api(req: MessageRequest):
    user_id = req.user_id
    username = req.username
    internal_id = get_internal_id(user_id, username)
    ConversationManager.clear_conversation(internal_id)
    return {"status": "ok", "message": "Memory cleared."}