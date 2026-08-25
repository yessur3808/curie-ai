"""Shared, local-only outbound voice synthesis and preference handling."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import shutil
import time
import asyncio
import re

_FRENCH_WORDS = frozenset({
    "bonjour", "salut", "merci", "oui", "non", "voilà", "alors", "donc",
    "bien", "sûr", "mon", "ami", "amie", "cœur", "chérie", "compris",
    "d’accord", "avec", "pour", "très", "bonne", "bon", "soir", "matin",
    "je", "tu", "vous", "nous", "est", "suis", "comment", "encore",
})


def detect_language_spans(text: str, accent: str = "subtle") -> list[tuple[str, str]]:
    """Split prose into English/French spans without sending text off-device."""
    if accent == "strong":
        return [("fr", text)]
    if accent == "neutral":
        return [("en", text)]
    pieces = re.findall(r"[^.!?\n]+(?:[.!?]+|\n|$)", text)
    spans: list[tuple[str, str]] = []
    for piece in pieces:
        if not piece.strip():
            continue
        words = re.findall(r"[a-zà-ÿ’']+", piece.casefold())
        french_hits = sum(word in _FRENCH_WORDS for word in words)
        accented = bool(re.search(r"[àâçéèêëîïôûùüÿœæ]", piece, re.I))
        language = "fr" if accented or french_hits >= max(1, len(words) // 3) else "en"
        if spans and spans[-1][0] == language:
            spans[-1] = (language, spans[-1][1] + piece)
        else:
            spans.append((language, piece))
    return spans or [("en", text)]


def _profile_config(preferences: dict, history: list[dict] | None = None) -> dict:
    profile = preferences.get("voice_profile", "soft")
    presets = {
        "clear": {"speed": "normal", "warmth": "neutral", "expressiveness": "calm"},
        "soft": {"speed": "normal", "warmth": "warm", "expressiveness": "calm"},
        "expressive": {"speed": "normal", "warmth": "gentle", "expressiveness": "expressive"},
        "french": {"speed": "slow", "warmth": "warm", "expressiveness": "balanced", "accent": "strong"},
    }
    configured = dict(presets.get(profile, presets["soft"]))
    explicit = {str(item.get("setting")) for item in (history or [])}
    for setting, key in (
        ("voice_speed", "speed"), ("voice_warmth", "warmth"),
        ("voice_expressiveness", "expressiveness"), ("voice_accent", "accent"),
    ):
        if setting in explicit:
            configured[key] = preferences.get(setting, configured.get(key))
    configured.update({
        "accent": configured.get("accent", "subtle"), "profile": profile,
        "custom_voice_consent": preferences.get("custom_voice_consent", False),
        "custom_voice_reference": preferences.get("custom_voice_reference", ""),
    })
    return configured


def voice_replies_enabled(internal_id: str, channel: str | None = None) -> bool:
    from memory.adaptation import get_preferences

    preferences = get_preferences(str(internal_id))
    if channel in set(preferences.get("voice_quiet_channels", [])):
        return False
    return bool(
        preferences.get("voice_reply_channels", {}).get(
            channel, preferences.get("voice_reply", False)
        )
    )


def voice_health() -> dict:
    """Report local voice readiness without loading or downloading a model."""
    from utils.voice import get_ffmpeg_executable, get_piper_executable

    piper_model = os.getenv("PIPER_MODEL_PATH", "").strip()
    english_model = os.getenv("PIPER_ENGLISH_MODEL_PATH", "").strip()
    piper_ready = bool(
        get_piper_executable()
        and piper_model
        and Path(piper_model).is_file()
    )
    espeak_ready = bool(shutil.which("espeak-ng") or shutil.which("espeak"))
    allow_espeak = os.getenv("LOCAL_TTS_ALLOW_ESPEAK", "true").lower() in {
        "1", "true", "yes", "on"
    }
    return {
        "ready": piper_ready or (allow_espeak and espeak_ready),
        "backend": "piper" if piper_ready else "espeak" if allow_espeak and espeak_ready else None,
        "piper_ready": piper_ready,
        "bilingual_ready": bool(
            piper_ready and english_model and Path(english_model).is_file()
        ),
        "offline_fallback_ready": allow_espeak and espeak_ready,
        "espeak_installed": espeak_ready,
        "quality_fallback_policy": "espeak" if allow_espeak else "text",
        "opus_encoder_ready": bool(get_ffmpeg_executable()),
    }


async def synthesize_reply(
    text: str, persona: dict, internal_id: str | None = None
) -> str | None:
    """Generate one local audio file; caller owns and must delete the result."""
    from utils.voice import (
        get_ffmpeg_executable,
        get_voice_config_from_persona,
        text_to_speech,
    )

    suffix = ".ogg" if get_ffmpeg_executable() else ".wav"
    fd, path = tempfile.mkstemp(prefix="curie_reply_", suffix=suffix)
    os.close(fd)
    started = time.perf_counter()
    try:
        config = get_voice_config_from_persona(persona)
        config.setdefault("accent", "french")
        preferences = {}
        if internal_id:
            from memory.adaptation import get_adaptation_state

            state = get_adaptation_state(str(internal_id))
            preferences = state["preferences"] if state["enabled"] else {}
            config.update(_profile_config(preferences, state.get("history", [])))
        if config.get("profile") == "custom":
            from services.custom_voice import synthesize_custom_voice

            custom_fd, custom_wav = tempfile.mkstemp(
                prefix="curie_custom_voice_", suffix=".wav"
            )
            os.close(custom_fd)
            try:
                if await synthesize_custom_voice(text, custom_wav, config):
                    if path.endswith(".wav"):
                        shutil.move(custom_wav, path)
                    else:
                        ffmpeg = get_ffmpeg_executable()
                        encoder = await asyncio.create_subprocess_exec(
                            ffmpeg, "-y", "-loglevel", "error", "-i", custom_wav,
                            "-c:a", "libopus", path,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await encoder.communicate()
                        if encoder.returncode:
                            return None
                    return path
            finally:
                Path(custom_wav).unlink(missing_ok=True)
        english_model = os.getenv("PIPER_ENGLISH_MODEL_PATH", "").strip()
        french_model = os.getenv("PIPER_FRENCH_MODEL_PATH", "").strip() or os.getenv("PIPER_MODEL_PATH", "").strip()
        spans = detect_language_spans(text, str(config.get("accent", "subtle")))
        if english_model and french_model and all(Path(item).is_file() for item in (english_model, french_model)):
            wav_paths = []
            try:
                for language, span in spans:
                    chunk_fd, chunk_path = tempfile.mkstemp(prefix="curie_voice_span_", suffix=".wav")
                    os.close(chunk_fd)
                    span_config = {**config, "model_path": french_model if language == "fr" else english_model}
                    if not await text_to_speech(span, chunk_path, span_config):
                        return None
                    wav_paths.append(chunk_path)
                if len(wav_paths) == 1:
                    if path.endswith(".wav"):
                        shutil.move(wav_paths[0], path)
                        wav_paths.clear()
                    else:
                        ffmpeg = get_ffmpeg_executable()
                        encoder = await asyncio.create_subprocess_exec(
                            ffmpeg, "-y", "-loglevel", "error", "-i", wav_paths[0],
                            "-c:a", "libopus", path,
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                        )
                        await encoder.communicate()
                        if encoder.returncode:
                            return None
                else:
                    ffmpeg = get_ffmpeg_executable()
                    args = [ffmpeg, "-y", "-loglevel", "error"]
                    for wav_path in wav_paths:
                        args.extend(["-i", wav_path])
                    args.extend(["-filter_complex", f"concat=n={len(wav_paths)}:v=0:a=1", "-c:a", "libopus", path])
                    encoder = await asyncio.create_subprocess_exec(
                        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                    )
                    await encoder.communicate()
                    if encoder.returncode:
                        return None
            finally:
                for wav_path in wav_paths:
                    Path(wav_path).unlink(missing_ok=True)
            generated = Path(path).is_file() and Path(path).stat().st_size > 0
        else:
            generated = await text_to_speech(text, path, config)
        if generated:
            from agent.observability import latency_metrics

            latency_metrics.observe(
                {"voice_synthesize": round((time.perf_counter() - started) * 1000, 2)}
            )
            return path
    except Exception:
        pass
    Path(path).unlink(missing_ok=True)
    return None
