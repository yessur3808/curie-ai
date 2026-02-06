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
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Accent to language code mapping for better recognition
ACCENT_LANGUAGE_MAP = {
    'american': 'en-US',
    'british': 'en-GB',
    'australian': 'en-AU',
    'indian': 'en-IN',
    'canadian': 'en-CA',
    'irish': 'en-IE',
    'scottish': 'en-GB',
    'french': 'fr-FR',
    'german': 'de-DE',
    'spanish': 'es-ES',
    'mexican': 'es-MX',
    'italian': 'it-IT',
    'portuguese': 'pt-PT',
    'brazilian': 'pt-BR',
    'russian': 'ru-RU',
    'japanese': 'ja-JP',
    'chinese': 'zh-CN',
    'korean': 'ko-KR',
    'arabic': 'ar-SA',
    'hindi': 'hi-IN',
}

# Voice settings for different TTS engines
VOICE_PROFILES = {
    'american': {'lang': 'en', 'tld': 'com', 'slow': False},
    'british': {'lang': 'en', 'tld': 'co.uk', 'slow': False},
    'australian': {'lang': 'en', 'tld': 'com.au', 'slow': False},
    'indian': {'lang': 'en', 'tld': 'co.in', 'slow': False},
    'french': {'lang': 'fr', 'tld': 'fr', 'slow': False},
    'german': {'lang': 'de', 'tld': 'de', 'slow': False},
    'spanish': {'lang': 'es', 'tld': 'es', 'slow': False},
    'mexican': {'lang': 'es', 'tld': 'com.mx', 'slow': False},
    'italian': {'lang': 'it', 'tld': 'it', 'slow': False},
    'portuguese': {'lang': 'pt', 'tld': 'pt', 'slow': False},
    'brazilian': {'lang': 'pt', 'tld': 'com.br', 'slow': False},
}


async def transcribe_audio(
    audio_path: str, 
    language: str = "en",
    accent: Optional[str] = None,
    auto_detect: bool = True
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
    # Map accent to language code if provided
    if accent and accent.lower() in ACCENT_LANGUAGE_MAP:
        language_code = ACCENT_LANGUAGE_MAP[accent.lower()]
        # Extract base language (e.g., 'en' from 'en-US')
        language = language_code.split('-')[0]
    
    try:
        # Try Whisper first (best quality, supports accent detection)
        return await transcribe_with_whisper(audio_path, language, auto_detect)
    except ImportError:
        logger.warning("Whisper not available, falling back to SpeechRecognition")
        try:
            return await transcribe_with_speech_recognition(audio_path, language, accent)
        except ImportError:
            logger.error("No speech recognition backend available")
            return None
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return None


async def transcribe_with_whisper(
    audio_path: str, 
    language: str = "en",
    auto_detect: bool = True
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
    
    logger.info(f"Loading Whisper model: {model_name}")
    model = whisper.load_model(model_name)
    
    # Transcribe with optional language hint
    logger.info(f"Transcribing audio: {audio_path}")
    if auto_detect:
        # Let Whisper auto-detect language and accent
        result = model.transcribe(audio_path)
        logger.info(f"Detected language: {result.get('language', 'unknown')}")
    else:
        result = model.transcribe(audio_path, language=language)
    
    return result["text"].strip()


async def transcribe_with_speech_recognition(
    audio_path: str, 
    language: str = "en",
    accent: Optional[str] = None
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
    
    # Use accent-specific language code if available
    if accent and accent.lower() in ACCENT_LANGUAGE_MAP:
        language = ACCENT_LANGUAGE_MAP[accent.lower()]
        logger.info(f"Using accent-specific language code: {language}")
    
    # Convert audio to WAV if needed
    audio_ext = Path(audio_path).suffix.lower()
    if audio_ext != '.wav':
        logger.info(f"Converting {audio_ext} to WAV")
        audio = AudioSegment.from_file(audio_path)
        wav_path = audio_path.rsplit('.', 1)[0] + '.wav'
        audio.export(wav_path, format='wav')
        audio_path = wav_path
    
    # Initialize recognizer
    recognizer = sr.Recognizer()
    
    # Load audio file
    with sr.AudioFile(audio_path) as source:
        audio_data = recognizer.record(source)
    
    # Transcribe using Google Speech Recognition with accent-aware language code
    try:
        text = recognizer.recognize_google(audio_data, language=language)
        return text
    except sr.UnknownValueError:
        logger.error("Could not understand audio")
        return ""
    except sr.RequestError as e:
        logger.error(f"Speech recognition service error: {e}")
        return ""


async def text_to_speech(
    text: str, 
    output_path: str, 
    voice_config: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Convert text to speech audio file with accent support.
    
    Args:
        text: Text to convert
        output_path: Path to save audio file
        voice_config: Voice configuration from persona (accent, language, speed, etc.)
                     Format: {
                         'accent': 'british',  # or 'american', 'indian', etc.
                         'language': 'en',     # base language
                         'speed': 'normal',    # 'slow', 'normal', 'fast'
                         'pitch': 'normal'     # future: pitch adjustment
                     }
        
    Returns:
        True if successful, False otherwise
    """
    try:
        return await text_to_speech_gtts(text, output_path, voice_config)
    except ImportError:
        logger.error("gTTS not available for text-to-speech")
        return False
    except Exception as e:
        logger.error(f"Text-to-speech failed: {e}")
        return False


async def text_to_speech_gtts(
    text: str, 
    output_path: str, 
    voice_config: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Convert text to speech using gTTS (Google Text-to-Speech) with accent support.
    gTTS supports different accents through TLD (top-level domain) parameter.
    
    Args:
        text: Text to convert
        output_path: Path to save audio file
        voice_config: Voice configuration with accent, language, speed
        
    Returns:
        True if successful, False otherwise
    """
    from gtts import gTTS
    
    # Parse voice configuration
    if voice_config is None:
        voice_config = {}
    
    accent = voice_config.get('accent', 'american').lower()
    language = voice_config.get('language', 'en')
    speed = voice_config.get('speed', 'normal')
    
    # Get voice profile for accent
    profile = VOICE_PROFILES.get(accent, VOICE_PROFILES['american'])
    
    # Override language if specified in config
    if language:
        profile['lang'] = language
    
    # Set speed
    slow = speed == 'slow' or profile.get('slow', False)
    
    logger.info(f"Converting text to speech: {len(text)} characters")
    logger.info(f"Voice config: accent={accent}, language={profile['lang']}, tld={profile.get('tld', 'com')}, slow={slow}")
    
    # Create TTS object with accent-specific settings
    try:
        tts = gTTS(
            text=text, 
            lang=profile['lang'], 
            slow=slow,
            tld=profile.get('tld', 'com')  # TLD controls accent
        )
        
        # Save to file
        tts.save(output_path)
        
        logger.info(f"Speech saved to: {output_path}")
        return True
    except Exception as e:
        logger.error(f"gTTS error: {e}")
        # Fallback to basic settings
        logger.info("Falling back to basic TTS without accent")
        tts = gTTS(text=text, lang=profile['lang'], slow=slow)
        tts.save(output_path)
        return True


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


def convert_audio_format(input_path: str, output_path: str, output_format: str = "mp3") -> bool:
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
        Voice configuration dictionary with accent, language, speed, etc.
    """
    voice_config = persona.get('voice', {})
    
    # Provide sensible defaults
    return {
        'accent': voice_config.get('accent', 'american'),
        'language': voice_config.get('language', persona.get('language', 'en')),
        'speed': voice_config.get('speed', 'normal'),
        'pitch': voice_config.get('pitch', 'normal'),
    }


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
    british_words = ['colour', 'favour', 'honour', 'realise', 'organise', 'whilst', 'amongst']
    if any(word in text_lower for word in british_words):
        return 'british'
    
    # Default to None (use Whisper's auto-detection)
    return None
