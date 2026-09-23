# utils/voice.py
"""
Voice processing utilities for speech-to-text and text-to-speech.
Supports multiple backends with accent-aware processing.
Features:
- Multi-accent speech recognition
- Persona-based voice synthesis with accent/dialect support
- Automatic language/accent detection
"""

import asyncio
import os
import logging
import shutil
import subprocess
import tempfile
import re
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


def get_piper_executable() -> Optional[str]:
    """Resolve Piper from PATH or Curie's project-local virtual environment."""
    configured = os.getenv("PIPER_BINARY", "piper").strip() or "piper"
    executable = shutil.which(configured)
    if executable:
        return executable
    if configured == "piper":
        local = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "piper"
        if local.is_file() and os.access(local, os.X_OK):
            return str(local)
    return None


# Cache for Whisper models to avoid reloading on each transcription
_whisper_model_cache = {}

# Accent to language code mapping for better recognition
ACCENT_LANGUAGE_MAP = {
    "american": "en-US",
    "british": "en-GB",
    "australian": "en-AU",
    "indian": "en-IN",
    "canadian": "en-CA",
    "irish": "en-IE",
    "scottish": "en-GB",
    "french": "fr-FR",
    "german": "de-DE",
    "spanish": "es-ES",
    "mexican": "es-MX",
    "italian": "it-IT",
    "portuguese": "pt-PT",
    "brazilian": "pt-BR",
    "russian": "ru-RU",
    "japanese": "ja-JP",
    "chinese": "zh-CN",
    "korean": "ko-KR",
    "arabic": "ar-SA",
    "hindi": "hi-IN",
}


def get_ffmpeg_executable() -> Optional[str]:
    """Find a system or project-local FFmpeg binary without downloading at runtime."""
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        return bundled if Path(bundled).is_file() else None
    except (ImportError, OSError):
        return None


def normalize_for_speech(text: str) -> str:
    """Make common written forms intelligible to offline speech engines."""
    text = re.sub(
        r"https?://([^/\s]+)(?:/\S*)?", lambda m: f"link to {m.group(1)}", text
    )
    replacements = {
        "API": "A P I",
        "CPU": "C P U",
        "GPU": "G P U",
        "RAM": "ram",
        "URL": "U R L",
        "AI": "A I",
        "°C": " degrees Celsius",
        "°F": " degrees Fahrenheit",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    text = re.sub(r"(?<=\d)%(?!\w)", " percent", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def transcribe_audio(
    audio_path: str,
    language: str = "en",
    accent: Optional[str] = None,
    auto_detect: bool = True,
) -> Optional[str]:
    """
    Transcribe audio file to text using Whisper or other speech recognition.
    Supports accent-aware recognition for better accuracy.

    Args:
        audio_path: Path to audio file
        language: Language code (default: "en")
        accent: Specific accent to optimize for (e.g., 'british', 'american', 'indian')
        auto_detect: If True, attempts to auto-detect language/accent

    Returns:
        Transcribed text or None if transcription fails
    """
    from services.api_voice_runtime import api_voice_runtime_status

    if api_voice_runtime_status()["active"] == "rust":
        try:
            from services.speech_runtime import transcribe_audio_native

            result = await transcribe_audio_native(
                audio_path,
                language=language,
                accent=accent,
                auto_detect=auto_detect,
            )
            return result["text"]
        except Exception as exc:
            logger.error("Native speech recognition failed: %s", exc)
            return None

    # Map accent to language code if provided
    if accent and accent.lower() in ACCENT_LANGUAGE_MAP:
        language_code = ACCENT_LANGUAGE_MAP[accent.lower()]
        # Extract base language (e.g., 'en' from 'en-US')
        language = language_code.split("-")[0]

    try:
        # Try Whisper first (best quality, supports accent detection)
        return await transcribe_with_whisper(audio_path, language, auto_detect)
    except ImportError:
        logger.warning("Whisper not available, falling back to SpeechRecognition")
        try:
            return await transcribe_with_speech_recognition(
                audio_path, language, accent
            )
        except ImportError:
            logger.error("No speech recognition backend available")
            return None
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return None


async def transcribe_with_whisper(
    audio_path: str, language: str = "en", auto_detect: bool = True
) -> str:
    """
    Transcribe audio using OpenAI Whisper with accent detection.
    Whisper is inherently good at handling different accents.

    Args:
        audio_path: Path to audio file
        language: Language code (can be None for auto-detection)
        auto_detect: If True, let Whisper auto-detect language

    Returns:
        Transcribed text
    """
    import whisper

    # Load model (using base model for balance between speed and accuracy)
    # Models: tiny, base, small, medium, large
    model_name = os.getenv("WHISPER_MODEL", "base")

    # Check cache first to avoid reloading
    if model_name in _whisper_model_cache:
        logger.info(f"Using cached Whisper model: {model_name}")
        model = _whisper_model_cache[model_name]
    else:
        logger.info(f"Loading Whisper model: {model_name}")
        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(None, whisper.load_model, model_name)
        _whisper_model_cache[model_name] = model
        logger.info(f"Cached Whisper model: {model_name}")

    # Transcribe with optional language hint
    logger.info(f"Transcribing audio: {audio_path}")
    if auto_detect:
        # Let Whisper auto-detect language and accent
        def _transcribe() -> dict:
            return model.transcribe(audio_path)

        result = await loop.run_in_executor(None, _transcribe)
        logger.info(f"Detected language: {result.get('language', 'unknown')}")
    else:

        def _transcribe_with_language() -> dict:
            return model.transcribe(audio_path, language=language)

        result = await loop.run_in_executor(None, _transcribe_with_language)

    return result["text"].strip()


async def transcribe_with_speech_recognition(
    audio_path: str, language: str = "en", accent: Optional[str] = None
) -> str:
    """
    Transcribe audio using SpeechRecognition library (Google Speech Recognition).
    Supports accent-specific language codes for better accuracy.

    Args:
        audio_path: Path to audio file
        language: Language code
        accent: Specific accent (e.g., 'british', 'indian')

    Returns:
        Transcribed text
    """
    import speech_recognition as sr
    from pydub import AudioSegment

    loop = asyncio.get_running_loop()
    converted_wav_path = None  # Track if we created a converted file

    # Use accent-specific language code if available
    if accent and accent.lower() in ACCENT_LANGUAGE_MAP:
        language = ACCENT_LANGUAGE_MAP[accent.lower()]
        logger.info(f"Using accent-specific language code: {language}")

    try:
        # Convert audio to WAV if needed (in executor to avoid blocking event loop)
        audio_ext = Path(audio_path).suffix.lower()
        if audio_ext != ".wav":
            logger.info(f"Converting {audio_ext} to WAV")

            def _convert_to_wav(path: str) -> str:
                audio = AudioSegment.from_file(path)
                wav_path = path.rsplit(".", 1)[0] + ".wav"
                audio.export(wav_path, format="wav")
                return wav_path

            converted_wav_path = await loop.run_in_executor(
                None, _convert_to_wav, audio_path
            )
            audio_path = converted_wav_path

        def _recognize(path: str, lang: str) -> str:
            # Initialize recognizer and perform recognition in executor
            recognizer = sr.Recognizer()
            try:
                with sr.AudioFile(path) as source:
                    audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data, language=lang)
                return text
            except sr.UnknownValueError:
                logger.error("Could not understand audio")
                return ""
            except sr.RequestError as e:
                logger.error(f"Speech recognition service error: {e}")
                return ""

        # Run speech recognition in executor to avoid blocking event loop
        text = await loop.run_in_executor(None, _recognize, audio_path, language)
        return text
    finally:
        # Clean up converted WAV file if we created one
        if converted_wav_path and os.path.exists(converted_wav_path):
            try:
                os.remove(converted_wav_path)
                logger.debug(f"Cleaned up converted WAV file: {converted_wav_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up converted WAV: {e}")


async def text_to_speech(
    text: str, output_path: str, voice_config: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Convert text to speech audio file with accent support and code-switching.

    Args:
        text: Text to convert
        output_path: Path to save audio file
        voice_config: Voice configuration from persona (accent, language, speed, code-switching, etc.)
                     Format: {
                         'accent': 'british',  # or 'american', 'indian', etc.
                         'language': 'en',     # base language
                         'speed': 'normal',    # 'slow', 'normal', 'fast'
                         'pitch': 'normal',    # future: pitch adjustment
                         'code_switching': {
                             'enabled': True,
                             'percentage': 15,
                             'native_language': 'fr'
                         }
                     }

    Returns:
        True if successful, False otherwise
    """
    config = voice_config or {}
    text = normalize_for_speech(text)
    if config:
        text = apply_code_switching(text, config)
    output = Path(output_path)
    fd, wav_path = tempfile.mkstemp(prefix="curie_tts_", suffix=".wav")
    os.close(fd)
    try:
        piper = get_piper_executable()
        model = str(
            config.get("model_path") or os.getenv("PIPER_MODEL_PATH", "")
        ).strip()
        if piper and model and Path(model).is_file():
            speed_scale = {"slow": 1.16, "normal": 1.0, "fast": 0.86}.get(
                config.get("speed"), 1.0
            )
            expression = config.get("expressiveness", "balanced")
            noise_scale = {"calm": 0.42, "balanced": 0.58, "expressive": 0.72}.get(
                expression, 0.58
            )
            noise_w_scale = {"calm": 0.55, "balanced": 0.70, "expressive": 0.82}.get(
                expression, 0.70
            )
            warmth = config.get("warmth", "gentle")
            sentence_silence = {"neutral": 0.12, "gentle": 0.20, "warm": 0.25}.get(
                warmth, 0.20
            )
            volume = {"neutral": 0.92, "gentle": 0.86, "warm": 0.82}.get(warmth, 0.86)
            process = await asyncio.create_subprocess_exec(
                piper,
                "--model",
                model,
                "--length-scale",
                str(speed_scale),
                "--noise-scale",
                str(noise_scale),
                "--noise-w-scale",
                str(noise_w_scale),
                "--sentence-silence",
                str(sentence_silence),
                "--volume",
                str(volume),
                "--output_file",
                wav_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            backend = "piper"
            _, stderr = await asyncio.wait_for(
                process.communicate(text.encode("utf-8")), timeout=120
            )
        else:
            allow_espeak = os.getenv("LOCAL_TTS_ALLOW_ESPEAK", "true").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if not allow_espeak:
                logger.warning(
                    "Neural TTS is unavailable and eSpeak is disabled; using text fallback"
                )
                return False
            espeak = shutil.which("espeak-ng") or shutil.which("espeak")
            if not espeak:
                logger.error("No local TTS backend found; install Piper or eSpeak")
                return False
            voice = os.getenv("LOCAL_TTS_ESPEAK_VOICE", "en+f4")
            speed_base = int(os.getenv("LOCAL_TTS_SPEED", "155"))
            speed = str(
                {"slow": speed_base - 25, "fast": speed_base + 25}.get(
                    config.get("speed"), speed_base
                )
            )
            pitch_base = int(os.getenv("LOCAL_TTS_PITCH", "58"))
            warmth = {"neutral": 0, "gentle": -3, "warm": -6}.get(
                config.get("warmth"), -3
            )
            expression = {"calm": -2, "balanced": 0, "expressive": 4}.get(
                config.get("expressiveness"), 0
            )
            pitch = str(max(0, min(99, pitch_base + warmth + expression)))
            process = await asyncio.create_subprocess_exec(
                espeak,
                "-v",
                voice,
                "-s",
                speed,
                "-p",
                pitch,
                "-w",
                wav_path,
                text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            backend = "espeak"
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        if process.returncode or not Path(wav_path).is_file():
            logger.error(
                "Local TTS (%s) failed: %s", backend, stderr.decode(errors="replace")
            )
            return False
        if output.suffix.casefold() == ".wav":
            shutil.move(wav_path, output)
            wav_path = ""
        else:
            ffmpeg = get_ffmpeg_executable()
            if not ffmpeg:
                logger.error(
                    "ffmpeg is required to encode %s voice replies", output.suffix
                )
                return False
            encoder = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                wav_path,
                "-c:a",
                "libopus",
                str(output),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, encode_error = await asyncio.wait_for(encoder.communicate(), timeout=120)
            if encoder.returncode:
                logger.error(
                    "Voice encoding failed: %s", encode_error.decode(errors="replace")
                )
                return False
        logger.info("Synthesized local speech with %s", backend)
        return output.is_file() and output.stat().st_size > 0
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        asyncio.TimeoutError,
    ) as exc:
        logger.error("Local text-to-speech failed: %s", exc)
        return False
    finally:
        if wav_path:
            Path(wav_path).unlink(missing_ok=True)


def get_audio_duration(audio_path: str) -> float:
    """
    Get duration of audio file in seconds.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in seconds
    """
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(audio_path)
        return len(audio) / 1000.0  # Convert milliseconds to seconds
    except Exception as e:
        logger.error(f"Failed to get audio duration: {e}")
        return 0.0


def convert_audio_format(
    input_path: str, output_path: str, output_format: str = "mp3"
) -> bool:
    """
    Convert audio file to different format.

    Args:
        input_path: Path to input audio file
        output_path: Path to output audio file
        output_format: Output format (mp3, wav, ogg, etc.)

    Returns:
        True if successful, False otherwise
    """
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(input_path)
        audio.export(output_path, format=output_format)
        logger.info(f"Converted {input_path} to {output_format}")
        return True
    except Exception as e:
        logger.error(f"Audio conversion failed: {e}")
        return False


def get_voice_config_from_persona(persona: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract voice configuration from persona object.

    Args:
        persona: Persona dictionary

    Returns:
        Voice configuration dictionary with accent, language, speed, code-switching, etc.
    """
    voice_config = persona.get("voice", {})

    # Provide sensible defaults
    config = {
        "accent": voice_config.get("accent", "american"),
        "language": voice_config.get("language", persona.get("language", "en")),
        "speed": voice_config.get("speed", "normal"),
        "pitch": voice_config.get("pitch", "normal"),
    }

    # Add code-switching configuration if present
    code_switching = voice_config.get("code_switching", {})
    if isinstance(code_switching, dict):
        config["code_switching"] = {
            "enabled": code_switching.get("enabled", False),
            "percentage": max(
                1, min(30, code_switching.get("percentage", 10))
            ),  # Clamp 1-30
            "native_language": code_switching.get("native_language", "fr"),
        }
    else:
        config["code_switching"] = {
            "enabled": False,
            "percentage": 10,
            "native_language": "fr",
        }

    return config


# Common code-switching expressions by language
CODE_SWITCHING_PHRASES = {
    "fr": {  # French
        "greetings": [
            "Bonjour",
            "Salut",
            "Bonsoir",
            "Oui",
            "Non",
            "Merci",
            "S'il vous plaît",
        ],
        "exclamations": [
            "Ah bon!",
            "Mais oui!",
            "C'est vrai!",
            "Bien sûr!",
            "Voilà!",
            "Eh bien",
        ],
        "fillers": ["alors", "donc", "eh bien", "tu vois", "vous savez", "quoi"],
        "phrases": ["C'est la vie", "Bon courage", "Comme ci comme ça", "Déjà vu"],
    },
    "de": {  # German
        "greetings": ["Guten Tag", "Hallo", "Tschüss", "Ja", "Nein", "Danke", "Bitte"],
        "exclamations": [
            "Ach so!",
            "Genau!",
            "Natürlich!",
            "Wunderbar!",
            "Prima!",
            "Ach ja",
        ],
        "fillers": ["also", "ja", "naja", "doch", "eben", "halt"],
        "phrases": ["Auf Wiedersehen", "Gute Nacht", "Viel Glück", "Prost"],
    },
    "es": {  # Spanish
        "greetings": [
            "Hola",
            "Buenos días",
            "Adiós",
            "Sí",
            "No",
            "Gracias",
            "Por favor",
        ],
        "exclamations": [
            "¡Claro!",
            "¡Por supuesto!",
            "¡Exacto!",
            "¡Perfecto!",
            "¡Vale!",
            "Bueno",
        ],
        "fillers": ["pues", "bueno", "entonces", "sabes", "o sea", "vale"],
        "phrases": ["Qué pasa", "No pasa nada", "Hasta luego", "Mucho gusto"],
    },
    "it": {  # Italian
        "greetings": [
            "Ciao",
            "Buongiorno",
            "Arrivederci",
            "Sì",
            "No",
            "Grazie",
            "Prego",
        ],
        "exclamations": [
            "Certo!",
            "Perfetto!",
            "Bene!",
            "Bravissimo!",
            "Ecco!",
            "Allora",
        ],
        "fillers": ["allora", "dunque", "cioè", "insomma", "dai", "boh"],
        "phrases": ["Come stai", "Va bene", "Mamma mia", "Che bello"],
    },
    "ar": {  # Arabic (transliterated)
        "greetings": ["Salam", "Marhaba", "Shukran", "Afwan", "Na'am", "La"],
        "exclamations": [
            "Yalla!",
            "Mashallah!",
            "Inshallah!",
            "Alhamdulillah!",
            "Wallah!",
            "Khalas",
        ],
        "fillers": ["yani", "khalas", "akeed", "tab", "walla", "sah"],
        "phrases": ["Inshallah", "Mashallah", "Ma'alesh", "Yalla habibi"],
    },
    "hi": {  # Hindi (transliterated)
        "greetings": ["Namaste", "Shukriya", "Dhanyavaad", "Haan", "Nahi", "Accha"],
        "exclamations": ["Arre!", "Acha!", "Bilkul!", "Zaroor!", "Shabash!", "Wah"],
        "fillers": ["accha", "haan", "nahi", "yaar", "bhai", "matlab"],
        "phrases": ["Koi baat nahi", "Theek hai", "Chalo", "Bas"],
    },
    "pt": {  # Portuguese
        "greetings": ["Olá", "Bom dia", "Tchau", "Sim", "Não", "Obrigado", "Por favor"],
        "exclamations": ["Claro!", "Exato!", "Perfeito!", "Ótimo!", "Puxa!", "Nossa"],
        "fillers": ["então", "né", "pois", "sabe", "tipo", "enfim"],
        "phrases": ["Tudo bem", "Que legal", "Valeu", "Beleza"],
    },
    "ru": {  # Russian (transliterated)
        "greetings": ["Privet", "Zdrastvuyte", "Spasibo", "Pozhaluysta", "Da", "Net"],
        "exclamations": [
            "Konechno!",
            "Tochno!",
            "Otlichno!",
            "Molodets!",
            "Nu!",
            "Vot",
        ],
        "fillers": ["nu", "vot", "tak", "znachit", "prosto", "kak-to"],
        "phrases": ["Do svidaniya", "Khorosho", "Oy bozhe moy", "Nichevo"],
    },
    "ja": {  # Japanese (romanized)
        "greetings": [
            "Konnichiwa",
            "Arigatou",
            "Sumimasen",
            "Hai",
            "Iie",
            "Onegaishimasu",
        ],
        "exclamations": [
            "Sou desu ne!",
            "Naruhodo!",
            "Sugoi!",
            "Yokatta!",
            "Maa!",
            "Ano",
        ],
        "fillers": ["ne", "ano", "etto", "demo", "sou", "maa"],
        "phrases": ["Ganbatte", "Omedetou", "Yoroshiku", "Ja ne"],
    },
    "zh": {  # Chinese (pinyin)
        "greetings": ["Nǐ hǎo", "Xièxiè", "Bù kèqì", "Shì", "Bù shì", "Qǐng"],
        "exclamations": ["Duì!", "Hǎo!", "Tài hǎo le!", "Zhēn de!", "Ò!", "Āi"],
        "fillers": ["nà", "jiù", "duì", "ma", "ne", "ba"],
        "phrases": ["Zàijiàn", "Méi guānxi", "Duìbuqǐ", "Hǎo ba"],
    },
}


def apply_code_switching(text: str, voice_config: Dict[str, Any]) -> str:
    """
    Apply code-switching to text by mixing in native language expressions.
    This adds authenticity and cultural charm to the response.

    Args:
        text: Original text in English
        voice_config: Voice configuration with code-switching settings

    Returns:
        Text with code-switched expressions mixed in
    """
    import random

    # Check if code-switching is enabled
    code_switching = voice_config.get("code_switching", {})
    if not code_switching.get("enabled", False):
        return text

    native_lang = code_switching.get("native_language", "fr")
    percentage = code_switching.get("percentage", 10)

    # Get phrases for the native language
    phrases = CODE_SWITCHING_PHRASES.get(native_lang, {})
    if not phrases:
        return text  # Language not supported yet

    # Collect all available phrases
    all_phrases = []
    for category in phrases.values():
        all_phrases.extend(category)

    if not all_phrases:
        return text

    # Split text into sentences
    import re

    sentences = re.split(r"([.!?]+\s+)", text)

    # Determine how many insertions to make based on percentage
    # percentage is 1-30, representing how frequently to insert (roughly per sentence)
    num_sentences = len([s for s in sentences if len(s.strip()) > 10])
    num_insertions = max(1, int(num_sentences * percentage / 100))

    # Randomly select sentences to modify (avoid consecutive modifications)
    sentence_indices = [i for i, s in enumerate(sentences) if len(s.strip()) > 10]
    if sentence_indices:
        selected_indices = random.sample(
            sentence_indices, min(num_insertions, len(sentence_indices))
        )

        for idx in selected_indices:
            phrase = random.choice(all_phrases)
            # Add phrase at beginning or end of sentence
            if random.random() < 0.5:
                # Beginning
                sentences[idx] = f"{phrase}, {sentences[idx]}"
            else:
                # End (before punctuation)
                sentences[idx] = sentences[idx].rstrip() + f", {phrase}"

    return "".join(sentences)


def detect_accent_from_text(text: str) -> Optional[str]:
    """
    Attempt to detect language/dialect from text patterns.
    This is a simple heuristic-based approach for language detection only.
    Note: This provides hints but should not be relied upon for critical decisions.
    The primary language detection should come from Whisper's built-in capabilities.

    Args:
        text: Text to analyze

    Returns:
        Detected language hint or None
    """
    text_lower = text.lower()

    # British English spelling patterns
    british_words = [
        "colour",
        "favour",
        "honour",
        "realise",
        "organise",
        "whilst",
        "amongst",
    ]
    if any(word in text_lower for word in british_words):
        return "british"

    # Default to None (use Whisper's auto-detection)
    return None
