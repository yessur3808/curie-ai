# Multi-Platform Support Guide

## Overview

Curie AI now supports multiple messaging platforms and voice interfaces, allowing you to interact with your AI assistant through your preferred channels.

## Supported Platforms

1. **Telegram** - Full support with text and voice messages
2. **Discord** - Bot with text, voice attachments, and commands
3. **WhatsApp** - Web-based connector with voice message support
4. **API/WebSocket** - RESTful API and WebSocket for custom integrations and Web UI

## Voice Interface Features

### Accent-Aware Speech Recognition
The system automatically adapts to different accents for better recognition accuracy:
- **American English** (en-US)
- **British English** (en-GB)
- **Australian English** (en-AU)
- **Indian English** (en-IN)
- **Canadian English** (en-CA)
- **Irish English** (en-IE)
- Plus support for French, German, Spanish, Italian, Portuguese, and more

### Text-to-Speech with Accent Support
The assistant can speak back in different accents based on the persona configuration:
- Configure accent in persona file
- Supports multiple languages and regional variants
- Natural-sounding voice synthesis

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Platform Tokens
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
DISCORD_BOT_TOKEN=your_discord_bot_token
WHATSAPP_SESSION_PATH=./whatsapp_session

# Enable/Disable Connectors
RUN_TELEGRAM=true
RUN_DISCORD=false
RUN_WHATSAPP=false
RUN_API=true

# Voice Configuration
WHISPER_MODEL=base  # Options: tiny, base, small, medium, large
```

### Persona Voice Configuration

Add a `voice` section to your persona JSON file:

```json
{
  "name": "Your Assistant",
  "language": "en",
  "voice": {
    "accent": "british",
    "language": "en",
    "speed": "normal",
    "pitch": "normal"
  }
}
```

**Supported Accents:**
- `american` - US English
- `british` - UK English
- `australian` - Australian English
- `indian` - Indian English
- `canadian` - Canadian English
- `french` - French
- `german` - German
- `spanish` - Spanish (Spain)
- `mexican` - Mexican Spanish
- `italian` - Italian
- `portuguese` - Portuguese (Portugal)
- `brazilian` - Brazilian Portuguese

## Platform-Specific Setup

### Telegram

1. Create a bot with [@BotFather](https://t.me/botfather)
2. Add token to `.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   RUN_TELEGRAM=true
   ```
3. Run: `python main.py --telegram`

**Voice Features:**
- Send voice messages directly in chat
- Bot automatically transcribes and responds
- Supports all Telegram voice message formats

### Discord

1. Create a Discord application at [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a bot and copy the token
3. Enable "Message Content Intent" in Bot settings
4. Add token to `.env`:
   ```bash
   DISCORD_BOT_TOKEN=your_bot_token_here
   RUN_DISCORD=true
   ```
5. Run: `python main.py --discord`

**Commands:**
- `!start` - Start conversation
- `!help` - Show help message
- `!busy` - Mark as busy
- `!resume` - Resume conversation
- `!identify <username>` - Link account
- `!clear_memory` - Clear conversation history (master users only)

**Voice Features:**
- Upload audio files to transcribe
- Bot processes and responds to audio attachments
- Supports common audio formats (mp3, wav, ogg)

### WhatsApp

1. Install WhatsApp connector:
   ```bash
   pip install whatsapp-web.py
   ```
2. Configure in `.env`:
   ```bash
   WHATSAPP_SESSION_PATH=./whatsapp_session
   RUN_WHATSAPP=true
   ```
3. Run: `python main.py --whatsapp`
4. Scan QR code with your WhatsApp mobile app

**Voice Features:**
- Send voice messages in WhatsApp chats
- Bot transcribes and responds automatically
- Session persists across restarts

### API / WebSocket

#### REST API Endpoints

**POST /chat** - Send a message
```json
{
  "user_id": "user123",
  "message": "Hello, how are you?",
  "voice_response": false,
  "idempotency_key": "optional-uuid"
}
```

**POST /transcribe** - Transcribe audio file
```bash
curl -X POST -F "file=@audio.mp3" \
     -F "language=en" \
     -F "accent=british" \
     http://localhost:8000/transcribe
```

**GET /audio/{filename}** - Download generated audio
```bash
curl http://localhost:8000/audio/voice_123.mp3 -o response.mp3
```

**GET /health** - Health check
```bash
curl http://localhost:8000/health
```

#### WebSocket

Connect to `ws://localhost:8000/ws/chat` for real-time chat:

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/chat');

// Send message
ws.send(JSON.stringify({
  user_id: "user123",
  message: "Hello!"
}));

// Receive response
ws.onmessage = (event) => {
  const response = JSON.parse(event.data);
  console.log(response.text);
};
```

## Running Multiple Connectors

You can run multiple platforms simultaneously:

```bash
# Run all connectors
python main.py --all

# Run specific connectors
python main.py --telegram --discord --api

# Use environment variables
RUN_TELEGRAM=true RUN_DISCORD=true RUN_API=true python main.py
```

## Voice Processing Details

### Speech-to-Text Backends

1. **OpenAI Whisper** (Recommended)
   - High accuracy
   - Automatic language detection
   - Supports 99+ languages
   - Offline processing
   - Install: `pip install openai-whisper`

2. **Google Speech Recognition** (Fallback)
   - Free tier available
   - Good accuracy
   - Requires internet connection
   - Install: `pip install SpeechRecognition`

### Text-to-Speech Backend

**Google Text-to-Speech (gTTS)**
- Supports multiple accents via TLD parameter
- Natural-sounding voices
- Free to use
- Requires internet connection
- Install: `pip install gTTS`

### Audio Format Support

**Input:** mp3, wav, ogg, m4a, flac, opus
**Output:** mp3, wav

## Dependencies

Install all required dependencies:

```bash
pip install -r requirements.txt
```

**Core dependencies:**
- `python-telegram-bot>=21.4` - Telegram support
- `discord.py>=2.4.0` - Discord support  
- `whatsapp-web.py>=0.0.8` - WhatsApp support
- `openai-whisper>=20240930` - Speech recognition
- `gTTS>=2.5.4` - Text-to-speech
- `pydub>=0.25.1` - Audio processing
- `SpeechRecognition>=3.12.0` - Alternative STT
- `websockets>=14.1` - WebSocket support
- `python-multipart>=0.0.20` - File uploads

## Troubleshooting

### Voice Recognition Issues

1. **Poor transcription quality:**
   - Try a larger Whisper model: `WHISPER_MODEL=medium`
   - Specify the accent in persona config
   - Ensure audio quality is good (clear, minimal background noise)

2. **"Whisper not available" error:**
   ```bash
   pip install openai-whisper
   # May require ffmpeg: apt-get install ffmpeg
   ```

3. **Accent not working correctly:**
   - Check persona `voice.accent` configuration
   - Verify accent is in supported list
   - Try `auto_detect=True` for automatic detection

### Discord Issues

1. **"Message content intent" error:**
   - Enable in Discord Developer Portal
   - Bot Settings → Privileged Gateway Intents → Message Content Intent

2. **Bot not responding:**
   - Verify bot has proper permissions in your server
   - Check bot is online in Discord
   - Review logs for errors

### WhatsApp Issues

1. **QR code not appearing:**
   - Check terminal output
   - Ensure port 18789 is not blocked
   - Try clearing session: `rm -rf ./whatsapp_session`

2. **Session expired:**
   - Re-scan QR code
   - WhatsApp Web sessions expire after ~2 weeks of inactivity

### API/WebSocket Issues

1. **CORS errors:**
   - Add CORS middleware if needed for web clients
   - Configure allowed origins

2. **File upload errors:**
   - Check file size limits
   - Verify content-type headers
   - Ensure `/tmp` directory is writable

## Advanced Features

### Custom Voice Profiles

Create custom voice profiles in `utils/voice.py`:

```python
VOICE_PROFILES = {
    'my_custom_accent': {
        'lang': 'en',
        'tld': 'co.uk',
        'slow': False
    }
}
```

### Accent Detection

The system can detect accents from text patterns:
- British English: "colour", "realise", "whilst"
- Indian English: "kindly", "do the needful", "prepone"
- Australian English: "mate", "arvo", "barbie"

### Voice Response Streaming

For real-time voice streaming (future enhancement):
- Use WebRTC for low-latency audio
- Stream TTS output as it's generated
- Support voice activity detection

## Security Considerations

1. **API Authentication:** Consider adding JWT or API key authentication
2. **Rate Limiting:** Implement rate limits to prevent abuse
3. **Input Validation:** All user inputs are sanitized
4. **Secure Tokens:** Never commit tokens to version control
5. **File Upload Limits:** Set appropriate size limits for audio files

## Performance Tips

1. **Whisper Model Selection:**
   - `tiny` - Fast, lower accuracy (~1GB RAM)
   - `base` - Balanced (default) (~1.5GB RAM)
   - `small` - Better accuracy (~2GB RAM)
   - `medium` - High accuracy (~5GB RAM)
   - `large` - Best accuracy (~10GB RAM)

2. **Concurrent Users:**
   - Use async/await for non-blocking operations
   - Configure worker threads for API
   - Monitor memory usage with multiple platforms

3. **Audio Processing:**
   - Convert to WAV for faster processing
   - Reduce sample rate if needed (16kHz sufficient for speech)
   - Use audio compression for storage

## Examples

### Example 1: British Assistant with Voice

```json
{
  "name": "Jarvis",
  "system_prompt": "You are Jarvis, a sophisticated British AI assistant...",
  "language": "en",
  "voice": {
    "accent": "british",
    "language": "en",
    "speed": "normal"
  }
}
```

### Example 2: French Assistant

```json
{
  "name": "Marie",
  "system_prompt": "Tu es Marie, une assistante IA française...",
  "language": "fr",
  "voice": {
    "accent": "french",
    "language": "fr",
    "speed": "normal"
  }
}
```

### Example 3: Multi-lingual Support

```json
{
  "name": "Polyglot",
  "system_prompt": "You are a multilingual assistant...",
  "language": "en",
  "voice": {
    "accent": "american",
    "language": "en",
    "speed": "normal"
  }
}
```

## Contributing

To add support for a new platform:

1. Create a new connector in `connectors/your_platform.py`
2. Follow the existing pattern (see `telegram.py` or `discord_bot.py`)
3. Implement normalized message format
4. Add voice support using `utils/voice.py`
5. Update `main.py` to register the connector
6. Add configuration to `.env.example`
7. Document in this guide

## Support

For issues or questions:
- GitHub Issues: [curie-ai/issues](https://github.com/yessur3808/curie-ai/issues)
- Documentation: Check README.md and this guide
- Logs: Review application logs for detailed error messages
