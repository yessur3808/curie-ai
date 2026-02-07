import os
import datetime
import uuid
import logging
import time
import threading
import re
from typing import Optional, NoReturn
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from agent.chat_workflow import ChatWorkflow
from memory import ConversationManager, UserManager
from utils.db import is_master_user

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="Curie AI API")

# Shared workflow instance (set by main.py)
_workflow = None

# Active WebSocket connections
active_connections = []
active_connections_lock = threading.Lock()

# Track generated voice files for cleanup (filename: timestamp)
_voice_files = {}
_voice_files_lock = threading.Lock()

# Voice file TTL in seconds (default: 1 hour)
VOICE_FILE_TTL = int(os.getenv("VOICE_FILE_TTL", "3600"))


def cleanup_old_voice_files() -> NoReturn:
    """
    Periodically clean up expired voice files.

    This function runs indefinitely in a daemon thread and never returns.
    It is designed to be run as a background task that continuously monitors
    and removes expired voice files from the /tmp directory.
    """
    while True:
        try:
            time.sleep(300)  # Run every 5 minutes
            current_time = time.time()
            with _voice_files_lock:
                expired_files = [
                    filename
                    for filename, created_time in _voice_files.items()
                    if current_time - created_time > VOICE_FILE_TTL
                ]
                for filename in expired_files:
                    file_path = os.path.join("/tmp", filename)
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            logger.info(f"Cleaned up expired voice file: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {filename}: {e}")
                    finally:
                        del _voice_files[filename]
        except Exception as e:
            logger.error(f"Error in voice file cleanup: {e}")


# Start cleanup thread
_cleanup_thread = threading.Thread(target=cleanup_old_voice_files, daemon=True)
_cleanup_thread.start()


def set_workflow(workflow: ChatWorkflow):
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def get_internal_id(user_id: str, username: Optional[str] = None) -> str:
    """
    Get or create internal user ID for API users.

    Args:
        user_id: External user ID from API client
        username: Optional username for the user

    Returns:
        Internal user ID (UUID string)
    """
    return UserManager.get_or_create_user_internal_id(
        channel="api",
        external_id=user_id,
        secret_username=username or user_id,
        updated_by="api",
    )


class MessageRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=256, pattern=r"^\S+.*\S+$|^\S$")
    message: str = Field(
        ..., min_length=1, max_length=10000, pattern=r"^\S+[\s\S]*\S+$|^\S$"
    )
    idempotency_key: Optional[str] = None  # Optional: for idempotency
    voice_response: bool = False  # Optional: request voice response
    username: Optional[str] = None  # Optional: username for memory management
    # Note: streaming not yet implemented
    # Pattern ensures no leading/trailing whitespace and non-empty content


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

    # Validate idempotency_key is safe for filesystem use (UUID format only)
    # This prevents path traversal attacks when generating voice files
    if req.idempotency_key and not re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        req.idempotency_key,
        re.IGNORECASE,
    ):
        raise HTTPException(
            status_code=400, detail="idempotency_key must be a valid UUID format"
        )

    # Normalize to standard ChatWorkflow format
    normalized_input = {
        "platform": "api",
        "external_user_id": req.user_id,
        "external_chat_id": req.user_id,  # For API, use user_id as chat_id
        "message_id": message_id,
        "text": req.message,
        "timestamp": datetime.datetime.utcnow(),
    }

    # Process through workflow
    result = await _workflow.process_message(normalized_input)

    # Generate voice response if requested
    voice_url = None
    if req.voice_response:
        try:
            from utils.voice import text_to_speech, get_voice_config_from_persona

            voice_config = get_voice_config_from_persona(_workflow.persona)

            # Generate voice file with server-generated UUID to prevent path traversal
            # Use a separate safe server-side filename instead of client-provided message_id
            voice_file_id = str(uuid.uuid4())
            voice_filename = f"voice_{voice_file_id}.mp3"
            voice_path = f"/tmp/{voice_filename}"

            success = await text_to_speech(result["text"], voice_path, voice_config)
            if success:
                voice_url = f"/audio/{voice_filename}"
                # Track file for cleanup
                with _voice_files_lock:
                    _voice_files[voice_filename] = time.time()
                logger.info(f"Generated voice response: {voice_url}")
        except Exception as e:
            logger.error(f"Failed to generate voice response: {e}")

    return MessageResponse(
        text=result["text"],
        timestamp=result["timestamp"].isoformat(),
        model_used=result["model_used"],
        processing_time_ms=result["processing_time_ms"],
        voice_url=voice_url,
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "workflow_initialized": _workflow is not None,
        "cache_stats": _workflow.get_cache_stats() if _workflow else {},
    }


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket endpoint for real-time chat.

    Client sends: {"user_id": "user123", "message": "Hello"}
    Server responds: {"text": "Response", "timestamp": "...", "model_used": "..."}
    """
    await websocket.accept()
    with active_connections_lock:
        active_connections.append(websocket)

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()

            if not _workflow:
                await websocket.send_json({"error": "System not initialized"})
                continue

            user_id = data.get("user_id")
            message = data.get("message")

            if not user_id or not message:
                await websocket.send_json({"error": "Missing user_id or message"})
                continue

            # Process message
            message_id = str(uuid.uuid4())
            normalized_input = {
                "platform": "websocket",
                "external_user_id": user_id,
                "external_chat_id": user_id,
                "message_id": message_id,
                "text": message,
                "timestamp": datetime.datetime.utcnow(),
            }

            result = await _workflow.process_message(normalized_input)

            # Send response
            await websocket.send_json(
                {
                    "text": result["text"],
                    "timestamp": result["timestamp"].isoformat(),
                    "model_used": result["model_used"],
                    "processing_time_ms": result["processing_time_ms"],
                }
            )

    except WebSocketDisconnect:
        with active_connections_lock:
            if websocket in active_connections:
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
            logger.debug(
                "Failed to send error message or close WebSocket", exc_info=True
            )
        finally:
            with active_connections_lock:
                if websocket in active_connections:
                    active_connections.remove(websocket)


@app.post("/transcribe")
async def transcribe_audio_api(
    file: UploadFile = File(...),
    user_id: str = None,
    language: str = "en",
    accent: str = None,
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
    ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus"}
    file_ext = os.path.splitext(file.filename)[1].lower()

    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Enforce file size limit (25MB)
    MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB in bytes

    temp_path = None
    try:
        from utils.voice import transcribe_audio

        # Save uploaded file temporarily with sanitized name
        safe_filename = f"upload_{uuid.uuid4()}{file_ext}"
        temp_path = os.path.join("/tmp", safe_filename)

        # Read file in chunks and enforce size limit
        total_size = 0
        with open(temp_path, "wb") as f:
            while chunk := await file.read(8192):  # Read in 8KB chunks
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413, detail=f"File too large. Maximum size is 25MB"
                    )
                f.write(chunk)

        # Transcribe
        transcribed_text = await transcribe_audio(
            temp_path, language=language, accent=accent, auto_detect=True
        )

        if not transcribed_text:
            raise HTTPException(status_code=400, detail="Failed to transcribe audio")

        return {"text": transcribed_text, "language": language}

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
    finally:
        # Clean up temporary file regardless of success or failure
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


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
    if not re.match(r"^[a-zA-Z0-9_\-]+\.(mp3|wav|ogg)$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = os.path.join("/tmp", filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Ensure the file is actually in /tmp (prevent path traversal)
    real_path = os.path.realpath(file_path)
    if not real_path.startswith("/tmp/"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Determine MIME type based on file extension
    ext = filename.rsplit(".", 1)[-1].lower()
    mime_types = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg"}
    media_type = mime_types.get(ext, "audio/mpeg")

    def iter_file():
        with open(file_path, "rb") as f:
            # Read in chunks for large files
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


class ClearMemoryRequest(BaseModel):
    user_id: Optional[str] = None
    username: Optional[str] = None


@app.post("/clear_memory")
async def clear_memory_api(req: ClearMemoryRequest):
    user_id = req.user_id
    username = req.username
    internal_id = get_internal_id(user_id, username)
    if not is_master_user(internal_id):
        raise HTTPException(status_code=403, detail="Not authorized to clear memory")
    ConversationManager.clear_conversation(internal_id)
    return {"status": "ok", "message": "Memory cleared."}
