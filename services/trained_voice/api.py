#!/usr/bin/env python3
"""Chat-only adapter for Curie's existing persona, model service and voice engine.
No tool router, task executor or outbound messaging connectors are loaded.
"""

import asyncio
import collections
from contextlib import asynccontextmanager
import datetime
import importlib.util
import json
import os
import pathlib
import re
import tempfile
import time
import uuid
from .bootstrap import ROOT
from .paths import VoicePaths
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from utils.persona import load_persona
from llm import manager
from agent.orchestration.model_service import ModelConversationService
from agent.orchestration.response_policy import ResponsePolicy
from agent.orchestration.social_service import SocialConversationService
from agent.personality_context import PersonalityContext
from .voice_persona import (
    dashboard_scope_prompt,
    finalize_live_response,
    handle_conversation_contract,
    live_conversation_prompt,
    max_recent_response_similarity,
    needs_english_rewrite,
    persona_prompt,
    select_relevant_history,
    is_unhelpful_repeat,
    is_topic_regression,
    turn_contract_prompt,
    delivery_settings,
    persona_revision,
)
from contracts.catalog import handle_capabilities_command
from .runtime import (
    TrainedVoiceBusy,
    TrainedVoiceError,
    synthesize_trained_voice,
    trained_voice_health,
    trained_voice_required,
    trained_voice_stream_events,
)
from .voice_stream import until_disconnect
from services.api_voice_runtime import decode, now_ms, voice_session_manager
from utils.voice import (
    text_to_speech,
    get_voice_config_from_persona,
    get_piper_executable,
    get_ffmpeg_executable,
)
import uvicorn

app = FastAPI(title="Curie Dashboard Conversation Bridge")
persona = load_persona()
model = ModelConversationService(manager)
social = SocialConversationService()
persona_file = os.getenv("PERSONA_FILE", "curie.json")
if not persona_file.endswith(".json"):
    persona_file += ".json"
persona_path = ROOT / "assets/personality" / persona_file
persona_mtime = persona_path.stat().st_mtime_ns


def refresh_persona():
    global persona, persona_mtime
    mtime = persona_path.stat().st_mtime_ns
    if mtime != persona_mtime:
        persona = load_persona()
        persona_mtime = mtime
    return persona


def active_voice_id(active=None):
    selected_persona = active or refresh_persona()
    voice = (
        selected_persona.get("voice")
        if isinstance(selected_persona.get("voice"), dict)
        else {}
    )
    value = str(voice.get("trained_id") or selected_persona.get("name") or "curie")
    return re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-") or "curie"


def active_voice_health(active=None):
    voice_id = active_voice_id(active)
    return (
        trained_voice_health()
        if voice_id in {"curie", "default"}
        else trained_voice_health(voice_id=voice_id)
    )


history = collections.OrderedDict()
gate = asyncio.Semaphore(1)
voice_paths = VoicePaths.from_root(ROOT)
VOICE_PYTHON = voice_paths.transcription_python
audio_dir = pathlib.Path(tempfile.mkdtemp(prefix="curie-dashboard-audio-"))
os.chmod(audio_dir, 0o700)


class Message(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    user_id: str = Field(min_length=1, max_length=256)
    voice_response: bool = False
    voice_profile: str = Field(
        default="trained", pattern="^(trained|french|clear|custom)$"
    )
    ephemeral: bool = False
    live: bool = False
    username: str | None = None
    dashboard: dict | None = None
    question: str | None = Field(default=None, max_length=2000)
    session_id: str | None = None


def voice_model(profile):
    key = (
        "PIPER_FRENCH_MODEL_PATH" if profile == "french" else "PIPER_ENGLISH_MODEL_PATH"
    )
    fallback = (
        "fr_FR-siwis-medium.onnx" if profile == "french" else "en_US-lessac-high.onnx"
    )
    return pathlib.Path(os.getenv(key) or ROOT / "models/voices" / fallback)


def custom_config():
    return {
        "custom_voice_consent": os.getenv("CURIE_CUSTOM_VOICE_ENABLED", "").lower()
        == "true",
        "custom_voice_reference": os.getenv("CURIE_VOICE_REFERENCE", ""),
    }


def reference_voice_ready():
    return active_voice_health()["ready"]


async def reference_voice(text, output, delivery):
    try:
        voice_id = active_voice_id()
        metrics = (
            await synthesize_trained_voice(text, output, delivery)
            if voice_id in {"curie", "default"}
            else await synthesize_trained_voice(
                text, output, delivery, voice_id=voice_id
            )
        )
    except TrainedVoiceBusy as error:
        raise HTTPException(
            429, "The trained voice is busy. Try again shortly."
        ) from error
    except TrainedVoiceError as error:
        if error.code == "trained_voice_timeout":
            raise HTTPException(
                504, "Voice generation timed out. Try a shorter message."
            ) from error
        raise HTTPException(
            503, "Curie's trained voice could not generate speech."
        ) from error
    print("Reference voice metrics: " + json.dumps(metrics, sort_keys=True), flush=True)


def voice_status():
    cfg = custom_config()
    custom_ready = bool(
        cfg["custom_voice_consent"]
        and all(
            pathlib.Path(os.getenv(k, "")).is_file()
            for k in ("XTTS_MODEL_PATH", "XTTS_CONFIG_PATH")
        )
        and pathlib.Path(cfg["custom_voice_reference"]).is_file()
        and importlib.util.find_spec("TTS")
    )
    piper = bool(get_piper_executable() and get_ffmpeg_executable())
    active = refresh_persona()
    trained_health = active_voice_health(active)
    reference = bool(trained_health["ready"])
    trained = bool(trained_health["trained"])
    return {
        "trained": reference,
        "default": "trained",
        "french": piper and voice_model("french").is_file(),
        "clear": piper and voice_model("clear").is_file(),
        "custom": reference_voice_ready() or custom_ready,
        "revision": trained_health.get("revision") or "reference-v1",
        "persona": persona.get("name"),
        "voiceId": active_voice_id(active),
        "personaRevision": persona_revision(persona),
        "owner": "curie-ai",
        "description": (
            (
                "Curie uses a locally fine-tuned Nano adapter, Curie reference audio and the active Curie personality for wording and delivery."
                if trained
                else "Curie uses reference conditioning; no model-weight fine-tuning is configured."
            )
            if reference
            else "French and English neural presets; custom voice is not configured."
        ),
    }


async def make_voice(text, profile, mode="professional"):
    active = refresh_persona()
    delivery = delivery_settings(active, mode)
    for old in audio_dir.glob("*"):
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    name = "voice_" + str(uuid.uuid4()) + ".ogg"
    path = audio_dir / name
    # Bounded input; no random code-switching can change factual headline/calendar text.
    text = re.sub(r"[*#`•]", "", text)
    if reference_voice_ready():
        with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
            await reference_voice(text, wav.name, delivery)
            ffmpeg = get_ffmpeg_executable()
            if not ffmpeg:
                raise HTTPException(503, "Voice encoding is unavailable")
            proc = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                wav.name,
                "-c:a",
                "libopus",
                str(path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode:
                raise HTTPException(503, "Voice encoding failed")
    elif trained_voice_required():
        raise HTTPException(
            503, "The active personality's trained voice is not available"
        )
    elif profile == "custom":
        with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
            from services.custom_voice import (
                synthesize_custom_voice,
                custom_voice_health,
            )

            cfg = custom_config()
            if not custom_voice_health(cfg)["ready"]:
                raise HTTPException(
                    503,
                    "Custom voice is not configured. Add the model and reference on the server.",
                )
            if not await synthesize_custom_voice(text, wav.name, cfg):
                raise HTTPException(503, "Custom voice synthesis failed")
            proc = await asyncio.create_subprocess_exec(
                get_ffmpeg_executable(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                wav.name,
                "-af",
                "atempo=" + str(delivery["rate"]),
                "-c:a",
                "libopus",
                str(path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode:
                raise HTTPException(503, "Voice encoding failed")
    else:
        selected = voice_model(profile)
        if not selected.is_file() or not get_piper_executable():
            raise HTTPException(503, "The selected neural voice model is not installed")
        cfg = {
            **get_voice_config_from_persona(persona),
            "model_path": str(selected),
            "speed": "normal",
            "warmth": "warm",
            "expressiveness": "calm",
            "code_switching": {"enabled": False},
        }
        if not await text_to_speech(text, str(path), cfg):
            raise HTTPException(503, "Neural voice synthesis is unavailable")
    sessions = voice_session_manager()
    if sessions is not None:
        sessions.register_artifact(name, now_ms(), 3_600_000)
    return "/audio/" + name


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=24000)
    voice_profile: str = Field(
        default="trained", pattern="^(trained|french|clear|custom)$"
    )
    user_id: str = Field(default="dashboard", min_length=1, max_length=256)
    session_id: str | None = None


def _open_voice_session(
    owner_id: str, *, requested_id: str | None = None, kind: str = "dashboard"
) -> dict | None:
    sessions = voice_session_manager()
    if sessions is None:
        return None
    return decode(
        sessions.open(
            owner_id,
            kind,
            now_ms(),
            int(os.getenv("CURIE_VOICE_SESSION_TTL_MS", "3600000")),
            requested_id,
            True,
        )
    )


@asynccontextmanager
async def _voice_operation(
    owner_id: str,
    operation: str,
    *,
    session_id: str | None = None,
    kind: str = "dashboard",
):
    sessions = voice_session_manager()
    if sessions is None:
        if gate.locked():
            raise HTTPException(429, "Curie is busy; try again shortly")
        async with gate:
            yield None, None
        return
    session = _open_voice_session(owner_id, requested_id=session_id, kind=kind)
    started = decode(
        sessions.start_operation(
            session["session_id"],
            operation,
            now_ms(),
            int(os.getenv("CURIE_VOICE_SESSION_TTL_MS", "3600000")),
        )
    )
    if started["outcome"] != "started":
        raise HTTPException(429, "Curie is busy with this live conversation")
    token = int(started["token"])
    try:
        yield session["session_id"], token
    except BaseException:
        sessions.cancel_active(session["session_id"], now_ms())
        raise
    else:
        sessions.finish_operation(session["session_id"], token, now_ms())


@app.post("/speak")
async def speak(req: Speech, request: Request):
    async with _voice_operation(
        req.user_id, "synthesis", session_id=req.session_id
    ) as (session_id, _):
        refresh_persona()
        status = voice_status()
        revision = str(status["revision"]) + ":" + status["personaRevision"]
        return {
            "voice_url": await until_disconnect(
                request, make_voice(req.text, req.voice_profile)
            ),
            "voice_profile": req.voice_profile,
            "voice_revision": revision,
            "session_id": session_id,
        }


@app.get("/health")
async def health():
    refresh_persona()
    sessions = voice_session_manager()
    session_health = decode(sessions.snapshot(now_ms())) if sessions is not None else {}
    return {
        "status": "healthy",
        "workflow_initialized": True,
        "adapter": "curie-conversation-only",
        "transcription": VOICE_PYTHON.is_file(),
        "speech": True,
        "liveVoice": True,
        "busy": bool(session_health.get("busy_sessions", gate.locked())),
        "voiceSessions": session_health,
        "voice": voice_status(),
    }


@app.post("/voice-sessions/{user_id}")
async def open_voice_session(user_id: str):
    session = _open_voice_session(user_id)
    if session is None:
        raise HTTPException(503, "Native live voice sessions are unavailable")
    return session


@app.get("/voice-sessions/session/{session_id}")
async def inspect_voice_session(session_id: str):
    sessions = voice_session_manager()
    if sessions is None:
        raise HTTPException(503, "Native live voice sessions are unavailable")
    try:
        return decode(sessions.inspect(session_id, now_ms()))
    except KeyError as exc:
        raise HTTPException(404, "Voice session not found") from exc


@app.post("/voice-sessions/session/{session_id}/cancel")
async def cancel_voice_session(session_id: str):
    sessions = voice_session_manager()
    if sessions is None:
        raise HTTPException(503, "Native live voice sessions are unavailable")
    try:
        snapshot = decode(sessions.inspect(session_id, now_ms()))
        active_token = snapshot.get("active_token")
        if active_token is not None:
            from llm.inference_service import get_inference_service

            get_inference_service().cancel(f"voice:{session_id}:{active_token}")
        return {"cancelled": sessions.cancel_active(session_id, now_ms())}
    except KeyError as exc:
        raise HTTPException(404, "Voice session not found") from exc


@app.delete("/voice-sessions/session/{session_id}")
async def close_voice_session(session_id: str):
    sessions = voice_session_manager()
    if sessions is None:
        raise HTTPException(503, "Native live voice sessions are unavailable")
    return {"closed": sessions.close(session_id)}


@app.post("/speak-stream")
async def speak_stream(req: Speech):
    if len(req.text) > 2000:
        raise HTTPException(400, "Live replies must be under 2,000 characters")

    async def events():
        async with _voice_operation(
            req.user_id, "synthesis", session_id=req.session_id
        ) as (session_id, _):
            yield json.dumps({"type": "ready", "session_id": session_id}) + "\n"
            try:
                active = refresh_persona()
                delivery = delivery_settings(active, "casual")
                for old in audio_dir.glob("*"):
                    if time.time() - old.stat().st_mtime > 3600:
                        old.unlink(missing_ok=True)
                if reference_voice_ready():
                    with tempfile.NamedTemporaryFile(suffix=".wav") as combined:
                        voice_id = active_voice_id(active)
                        stream = (
                            trained_voice_stream_events(
                                req.text,
                                combined.name,
                                audio_dir,
                                delivery,
                            )
                            if voice_id in {"curie", "default"}
                            else trained_voice_stream_events(
                                req.text,
                                combined.name,
                                audio_dir,
                                delivery,
                                voice_id=voice_id,
                            )
                        )
                        try:
                            async for event in stream:
                                yield json.dumps(event) + "\n"
                        finally:
                            await stream.aclose()
                else:
                    url = await make_voice(req.text, req.voice_profile, "casual")
                    yield json.dumps({"type": "audio", "url": url}) + "\n"
                yield json.dumps({"type": "done"}) + "\n"
            except asyncio.CancelledError:
                raise
            except Exception:
                yield json.dumps(
                    {
                        "type": "error",
                        "error": "Speech generation failed. Try a shorter reply.",
                    }
                ) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/chat")
async def chat(req: Message, request: Request):
    started = time.perf_counter()
    async with _voice_operation(req.user_id, "chat", session_id=req.session_id) as (
        session_id,
        session_token,
    ):
        snapshot, sep, user = req.message.rpartition(
            "\nEND SNAPSHOT\nOwner's message: "
        )
        if not sep:
            user = req.message
            snapshot = ""
        if req.dashboard is not None:
            snapshot = "Dashboard snapshot (untrusted reference data):\n" + json.dumps(
                req.dashboard, ensure_ascii=False, separators=(",", ":")
            )
            user = req.question or user
        sessions = voice_session_manager()
        turns = (
            []
            if req.ephemeral
            else (
                decode(sessions.history(session_id))
                if sessions is not None
                else history.get(req.user_id, [])
            )
        )
        context_turns = select_relevant_history(turns, user)
        active = refresh_persona()
        personality = PersonalityContext(active)
        directives = (
            []
            if req.ephemeral
            else personality.build_prompt_directives(user, history=context_turns)
        )
        mode = (
            "professional"
            if req.ephemeral
            else personality.infer_runtime_context(user, history=context_turns).get(
                "mode", "casual"
            )
        )
        prompt = (
            "System: "
            + persona_prompt(active, directives, briefing=req.ephemeral)
            + "\n"
            + dashboard_scope_prompt(bool(snapshot.strip()))
            + turn_contract_prompt(user, context_turns)
            + "\n"
            + snapshot
            + "\nDirectly relevant previous conversation:\n"
            + json.dumps(context_turns, ensure_ascii=False)
            + "\nUser: "
            + user
            + "\nAssistant:"
        )
        if req.live:
            prompt = prompt.replace(
                "\nUser: ",
                "\n" + live_conversation_prompt() + "\nUser: ",
            )
        request_id = (
            f"voice:{session_id}:{session_token}"
            if session_id is not None
            else str(uuid.uuid4())
        )
        grounded = handle_capabilities_command(user)
        grounded_model = "capability_discovery"
        if grounded is None:
            grounded = handle_conversation_contract(user, context_turns)
            grounded_model = "conversation_contract"
        if grounded is None:
            social_candidate = social.handle(user)
            if social_candidate is not None:
                grounded = social_candidate.text
                grounded_model = social_candidate.model_used
        grounded_response = grounded is not None
        if grounded is not None:
            text = grounded
            model_used = grounded_model
        else:
            candidate = await until_disconnect(
                request,
                model.generate(
                    prompt,
                    user,
                    0.35,
                    owner_id="dashboard:" + req.user_id,
                    request_id=request_id,
                ),
            )
            text = candidate.text or "No response was available."
            text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
            if text.startswith("[Error"):
                raise HTTPException(503, "Curie’s configured model could not answer")
            model_used = candidate.model_used
            if needs_english_rewrite(text, user):
                rewrite_prompt = (
                    "Rewrite this reply in natural, grammatically correct English. Preserve "
                    "its meaning, warmth, brevity, and Curie's understated personality. Do "
                    "not add facts, advice, a question, or French. Return only the reply.\n\n"
                    f"Reply:\n{text}\n\nNatural English reply:"
                )
                rewritten = await until_disconnect(
                    request,
                    model.generate(
                        rewrite_prompt,
                        user,
                        0.2,
                        owner_id="dashboard:" + req.user_id,
                        request_id=request_id + ":english-rewrite",
                    ),
                )
                candidate_text = re.sub(
                    r"<think>[\s\S]*?</think>", "", rewritten.text or ""
                ).strip()
                if (
                    candidate_text
                    and not candidate_text.startswith("[Error")
                    and not needs_english_rewrite(candidate_text, user)
                ):
                    text = candidate_text
                    model_used = rewritten.model_used + ":english_rewrite"
        if not req.ephemeral:
            response_policy = ResponsePolicy(
                active, personality, minimal_sanitization=True
            )
            text = (
                response_policy.sanitize(text)
                if grounded_response
                else response_policy.finalize(text, user, history=turns)
            )
            if req.live:
                text = finalize_live_response(
                    text, user, has_snapshot=bool(snapshot.strip())
                )
            repeated_reply = is_unhelpful_repeat(text, turns, user)
            topic_regression = is_topic_regression(text, context_turns, turns, user)
            if not grounded_response and (repeated_reply or topic_regression):
                original_similarity = max_recent_response_similarity(text, turns)
                repair_prompt = (
                    "System: "
                    + persona_prompt(active, directives)
                    + "\n"
                    + dashboard_scope_prompt(bool(snapshot.strip()))
                    + turn_contract_prompt(user, context_turns)
                    + "\nThe draft below either repeats an earlier answer or answers "
                    "an older subject instead of the immediately preceding exchange. "
                    "Answer the owner's newest message directly with new, relevant wording. "
                    "Do not recap the old answer, restart the old topic, apologize, or mention "
                    "this repair instruction.\nRecent conversation:\n"
                    + json.dumps(turns[-3:], ensure_ascii=False)
                    + "\nNewest owner message: "
                    + user
                    + "\nRepeated draft: "
                    + text
                    + "\nReplacement answer:"
                )
                repaired = await until_disconnect(
                    request,
                    model.generate(
                        repair_prompt,
                        user,
                        0.25,
                        owner_id="dashboard:" + req.user_id,
                        request_id=request_id + ":continuity-repair",
                    ),
                )
                repaired_text = re.sub(
                    r"<think>[\s\S]*?</think>", "", repaired.text or ""
                ).strip()
                if repaired_text and not repaired_text.startswith("[Error"):
                    repaired_text = response_policy.finalize(
                        repaired_text, user, history=turns
                    )
                    repaired_similarity = max_recent_response_similarity(
                        repaired_text, turns
                    )
                    repaired_regression = is_topic_regression(
                        repaired_text, context_turns, turns, user
                    )
                    if (
                        repeated_reply and repaired_similarity < original_similarity
                    ) or (topic_regression and not repaired_regression):
                        text = repaired_text
                        model_used = repaired.model_used + ":continuity_repair"
        if not req.ephemeral:
            turns = (turns + [{"user": user[:2000], "assistant": text[:3000]}])[-4:]
            if sessions is not None:
                sessions.append_history(session_id, user, text)
            else:
                history[req.user_id] = turns
                history.move_to_end(req.user_id)
                while len(history) > 20:
                    history.popitem(last=False)
        voice_url = None
        if req.voice_response:
            try:
                if sessions is not None:
                    sessions.transition(
                        session_id, session_token, "synthesizing", now_ms()
                    )
                voice_url = await until_disconnect(
                    request, make_voice(text[:24000], req.voice_profile, mode)
                )
            except Exception:
                pass
        return {
            "text": text,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "model_used": model_used,
            "processing_time_ms": round((time.perf_counter() - started) * 1000),
            "voice_url": voice_url,
            "session_id": session_id,
        }


@app.get("/audio/{name}")
async def audio(name: str):
    from services.api_voice_runtime import required_native

    native = required_native()
    if native is not None:
        try:
            native.validate_audio_artifact_name(name)
        except ValueError as exc:
            raise HTTPException(404) from exc
    if (
        not re.fullmatch(r"voice_[a-f0-9-]+\.(ogg|wav)", name)
        or not (audio_dir / name).is_file()
    ):
        raise HTTPException(404)
    return FileResponse(
        audio_dir / name,
        media_type="audio/wav" if name.endswith(".wav") else "audio/ogg",
        headers={"Cache-Control": "private, no-store"},
    )


@app.post("/transcribe")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Form("dashboard"),
    language: str = Form("en"),
    session_id: str | None = Form(None),
):
    if not VOICE_PYTHON.is_file():
        raise HTTPException(503, "Install dashboard voice dependencies first")
    suffix = pathlib.Path(file.filename or "").suffix.lower()
    if suffix not in {".wav", ".ogg", ".m4a", ".mp3", ".webm", ".opus", ".flac"}:
        raise HTTPException(400, "Unsupported audio format")
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Audio is too large")
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        async with _voice_operation(
            user_id, "transcription", session_id=session_id
        ) as (active_session, _):
            from services.speech_runtime import (
                SpeechRecognitionError,
                transcribe_audio_native,
            )

            try:
                result = await until_disconnect(
                    request,
                    transcribe_audio_native(
                        f.name,
                        language=language,
                        auto_detect=False,
                        root=ROOT,
                    ),
                )
            except SpeechRecognitionError as error:
                raise HTTPException(
                    503,
                    "Speech recognition unavailable; check the local voice model installation",
                ) from error
            return {**result, "session_id": active_session}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("CURIE_API_PORT", "8010")),
        log_level="warning",
    )
