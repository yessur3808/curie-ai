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
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field
from agent.chat_workflow import ChatWorkflow
from memory import UserManager
from memory.session_store import get_session_manager
from utils.db import is_master_user

load_dotenv()
logger = logging.getLogger(__name__)

# Compiled UUID regex used to validate idempotency_key values.
# Exported at module level so tests can import the production pattern
# instead of maintaining a duplicate.
_IDEMPOTENCY_KEY_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

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
    if req.idempotency_key and not _IDEMPOTENCY_KEY_RE.match(req.idempotency_key):
        raise HTTPException(
            status_code=400, detail="idempotency_key must be a valid UUID format"
        )

    # Normalize to standard ChatWorkflow format
    # Compute internal_id upfront using the same helper as /clear_memory so
    # all API endpoints resolve to the same profile for a given user_id.
    internal_id = get_internal_id(req.user_id, req.username)
    normalized_input = {
        "platform": "api",
        "external_user_id": req.user_id,
        "external_chat_id": req.user_id,  # For API, use user_id as chat_id
        "message_id": message_id,
        "text": req.message,
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
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


@app.get("/reminders")
async def list_reminders_api(user_id: str):
    """
    List upcoming reminders for a user.

    Example: GET /reminders?user_id=user123
    """
    if not _workflow:
        raise HTTPException(status_code=500, detail="System not initialized")

    message_id = str(uuid.uuid4())
    internal_id = get_internal_id(user_id)
    normalized_input = {
        "platform": "api",
        "external_user_id": user_id,
        "external_chat_id": user_id,
        "message_id": message_id,
        "text": "list my reminders",
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }
    result = await _workflow.process_message(normalized_input)
    return {"text": result["text"], "model_used": result["model_used"]}


@app.delete("/reminders")
async def delete_reminders_api(user_id: str, index: Optional[int] = None):
    """
    Delete a reminder (or all reminders) for a user.

    - DELETE /reminders?user_id=user123          → delete all
    - DELETE /reminders?user_id=user123&index=1  → delete reminder #1
    """
    if not _workflow:
        raise HTTPException(status_code=500, detail="System not initialized")

    text = f"delete reminder {index}" if index is not None else "cancel all reminders"
    message_id = str(uuid.uuid4())
    internal_id = get_internal_id(user_id)
    normalized_input = {
        "platform": "api",
        "external_user_id": user_id,
        "external_chat_id": user_id,
        "message_id": message_id,
        "text": text,
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }
    result = await _workflow.process_message(normalized_input)
    return {"text": result["text"], "model_used": result["model_used"]}


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
            internal_id = get_internal_id(user_id)
            normalized_input = {
                "platform": "api",
                "external_user_id": user_id,
                "external_chat_id": user_id,
                "message_id": message_id,
                "text": message,
                "timestamp": datetime.datetime.utcnow(),
                "internal_id": internal_id,
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
                        status_code=413, detail="File too large. Maximum size is 25MB"
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
    get_session_manager().reset_user_all_channels(internal_id)
    return {"status": "ok", "message": "Memory cleared."}


# ---------------------------------------------------------------------------
# WebChat UI — served at GET /  (browser-based chat client)
# ---------------------------------------------------------------------------

_WEBCHAT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curie AI – WebChat</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:linear-gradient(135deg,#0a0e1a 0%,#0d1b2e 60%,#0a1628 100%);
  min-height:100vh;
  font-family:'Segoe UI',system-ui,sans-serif;
  color:#e8f4ff;
  display:flex;flex-direction:column;
}
/* ── Stars background ── */
#stars{position:fixed;inset:0;pointer-events:none;z-index:0}
.star{position:absolute;border-radius:50%;background:#fff;
  animation:twinkle var(--d,3s) infinite var(--delay,0s)}
@keyframes twinkle{0%,100%{opacity:.2}50%{opacity:1}}
/* ── Layout ── */
#app{position:relative;z-index:1;display:flex;flex-direction:column;
  height:100vh;max-width:900px;margin:0 auto;width:100%;padding:0 12px}
/* ── Header ── */
header{text-align:center;padding:20px 0 12px}
header h1{
  font-size:1.8rem;font-weight:700;letter-spacing:.04em;
  background:linear-gradient(90deg,#7dd3fc,#38bdf8,#0ea5e9,#a78bfa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
header p{color:#94a3b8;font-size:.85rem;margin-top:4px}
/* ── Connection status ── */
#status-bar{
  display:flex;align-items:center;gap:8px;justify-content:center;
  padding:6px 0;font-size:.8rem;color:#64748b}
#status-dot{width:8px;height:8px;border-radius:50%;background:#64748b;
  transition:background .3s}
#status-dot.connected{background:#22c55e;box-shadow:0 0 6px #22c55e}
#status-dot.connecting{background:#f59e0b;animation:pulse 1s infinite}
#status-dot.error{background:#ef4444}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
/* ── Messages area ── */
#messages{
  flex:1;overflow-y:auto;padding:16px 0;display:flex;flex-direction:column;gap:12px;
  scrollbar-width:thin;scrollbar-color:#1e3a5f transparent}
#messages::-webkit-scrollbar{width:4px}
#messages::-webkit-scrollbar-track{background:transparent}
#messages::-webkit-scrollbar-thumb{background:#1e3a5f;border-radius:2px}
/* ── Message bubbles ── */
.msg{display:flex;gap:10px;max-width:78%;animation:fadeUp .25s ease}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.msg.user{align-self:flex-end;flex-direction:row-reverse}
.msg.bot{align-self:flex-start}
.avatar{
  width:32px;height:32px;border-radius:50%;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:1rem}
.msg.user .avatar{background:linear-gradient(135deg,#1d4ed8,#7c3aed)}
.msg.bot  .avatar{background:linear-gradient(135deg,#0e7490,#0369a1)}
.bubble{
  padding:10px 14px;border-radius:16px;font-size:.92rem;line-height:1.5;
  max-width:100%;word-break:break-word;white-space:pre-wrap}
.msg.user .bubble{
  background:linear-gradient(135deg,#1d4ed8,#2563eb);
  border-bottom-right-radius:4px;color:#fff}
.msg.bot .bubble{
  background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);
  border-bottom-left-radius:4px;color:#e2eeff}
.meta{font-size:.72rem;color:#64748b;margin-top:3px;text-align:right}
.msg.bot .meta{text-align:left}
/* ── Typing indicator ── */
#typing{display:none;align-self:flex-start;padding:8px 16px;
  background:rgba(255,255,255,.05);border-radius:16px;border-bottom-left-radius:4px}
#typing span{display:inline-block;width:7px;height:7px;border-radius:50%;
  background:#38bdf8;animation:bounce .8s infinite}
#typing span:nth-child(2){animation-delay:.15s}
#typing span:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
/* ── Input area ── */
#input-area{
  display:flex;gap:10px;padding:12px 0 20px;
  border-top:1px solid rgba(255,255,255,.06)}
#msg-input{
  flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);
  border-radius:24px;padding:10px 18px;color:#e8f4ff;font-size:.95rem;
  resize:none;outline:none;transition:border-color .2s;min-height:44px;max-height:140px;
  font-family:inherit;overflow-y:auto}
#msg-input:focus{border-color:#38bdf8}
#msg-input::placeholder{color:#475569}
#send-btn{
  width:44px;height:44px;border-radius:50%;border:none;cursor:pointer;flex-shrink:0;
  background:linear-gradient(135deg,#0ea5e9,#7c3aed);
  display:flex;align-items:center;justify-content:center;
  transition:opacity .2s,transform .1s}
#send-btn:hover{opacity:.85}
#send-btn:active{transform:scale(.93)}
#send-btn svg{fill:#fff;width:20px;height:20px}
#user-id-row{
  display:flex;align-items:center;gap:8px;padding:8px 0;
  border-bottom:1px solid rgba(255,255,255,.05);margin-bottom:4px}
#user-id-row label{font-size:.78rem;color:#64748b;white-space:nowrap}
#user-id-input{
  flex:1;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);
  border-radius:8px;padding:4px 10px;color:#94a3b8;font-size:.78rem;outline:none}
</style>
</head>
<body>
<div id="stars"></div>
<div id="app">
  <header>
    <h1>✦ Curie AI</h1>
    <p>Your personal AI assistant</p>
  </header>
  <div id="user-id-row">
    <label for="user-id-input">User ID:</label>
    <input id="user-id-input" type="text" placeholder="guest" value="guest" spellcheck="false">
  </div>
  <div id="status-bar">
    <div id="status-dot" class="connecting"></div>
    <span id="status-text">Connecting…</span>
  </div>
  <div id="messages" role="log" aria-live="polite"></div>
  <div id="typing"><span></span><span></span><span></span></div>
  <div id="input-area">
    <textarea id="msg-input" rows="1" placeholder="Type a message…" autocomplete="off"></textarea>
    <button id="send-btn" title="Send">
      <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
    </button>
  </div>
</div>
<script>
// ── Stars ──────────────────────────────────────────────────────────────────
(function(){
  const c=document.getElementById('stars');
  for(let i=0;i<80;i++){
    const s=document.createElement('div');
    s.className='star';
    const sz=Math.random()*2+.5;
    s.style.cssText=`left:${Math.random()*100}%;top:${Math.random()*100}%;
      width:${sz}px;height:${sz}px;--d:${(Math.random()*4+2).toFixed(1)}s;
      --delay:-${(Math.random()*4).toFixed(1)}s`;
    c.appendChild(s);
  }
})();

// ── WebSocket chat ──────────────────────────────────────────────────────────
const proto   = location.protocol==='https:'?'wss':'ws';
const wsUrl   = proto+'://'+location.host+'/ws/chat';
const messages= document.getElementById('messages');
const typing  = document.getElementById('typing');
const input   = document.getElementById('msg-input');
const sendBtn = document.getElementById('send-btn');
const dot     = document.getElementById('status-dot');
const statusTxt=document.getElementById('status-text');
const userIdEl= document.getElementById('user-id-input');

let ws, reconnectTimer;

function setStatus(s,text){
  dot.className=s; statusTxt.textContent=text;
}

function connect(){
  setStatus('connecting','Connecting…');
  ws=new WebSocket(wsUrl);
  ws.onopen=()=>{ setStatus('connected','Connected'); };
  ws.onclose=()=>{
    setStatus('error','Disconnected – retrying in 3 s…');
    reconnectTimer=setTimeout(connect,3000);
  };
  ws.onerror=()=>setStatus('error','Connection error');
  ws.onmessage=(evt)=>{
    typing.style.display='none';
    const d=JSON.parse(evt.data);
    if(d.error){ appendMsg('bot','⚠ '+d.error,''); return; }
    const ms=d.processing_time_ms?`${d.processing_time_ms.toFixed(0)} ms`:'';
    appendMsg('bot',d.text,ms);
    scrollDown();
  };
}

function appendMsg(role,text,meta){
  const wrap=document.createElement('div');
  wrap.className='msg '+role;
  const av=document.createElement('div'); av.className='avatar';
  av.textContent=role==='user'?'🧑':'🤖';
  const bub=document.createElement('div'); bub.className='bubble';
  bub.textContent=text;
  const m=document.createElement('div'); m.className='meta';
  m.textContent=meta||new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  wrap.appendChild(av);
  const inner=document.createElement('div');
  inner.appendChild(bub); inner.appendChild(m);
  wrap.appendChild(inner);
  messages.appendChild(wrap);
  scrollDown();
}

function scrollDown(){
  messages.scrollTop=messages.scrollHeight;
}

function sendMessage(){
  const text=input.value.trim();
  if(!text||!ws||ws.readyState!==1) return;
  const uid=userIdEl.value.trim()||'guest';
  appendMsg('user',text,'');
  input.value=''; input.style.height='';
  typing.style.display='flex';
  scrollDown();
  ws.send(JSON.stringify({user_id:uid,message:text}));
}

sendBtn.addEventListener('click',sendMessage);
input.addEventListener('keydown',(e)=>{
  if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); sendMessage(); }
});
input.addEventListener('input',()=>{
  input.style.height='auto';
  input.style.height=Math.min(input.scrollHeight,140)+'px';
});

// Greeting
appendMsg('bot','👋 Hello! I\'m Curie. How can I help you today?','');
connect();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def webchat_ui():
    """Serve the browser-based WebChat UI."""
    return HTMLResponse(content=_WEBCHAT_HTML)
