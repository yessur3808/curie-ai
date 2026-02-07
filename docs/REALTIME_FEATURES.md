# Real-Time Information & Proactive Messaging Features

This document describes the new features added to make Curie more helpful, accurate, and caring.

## Features

### 1. Real-Time Date & Time Access

Curie now has accurate access to the current date and time in any timezone.

**What changed:**
- Added `utils/datetime_info.py` with timezone-aware date/time functions
- Integrated with agent routing as `date_time` action
- Current date/time is automatically injected into every conversation prompt

**Example queries:**
- "What's today's date?"
- "What time is it in Hong Kong?"
- "Tell me the current date and time in New York"

**How it works:**
- Extracts timezone from user's message or profile
- Returns formatted date/time with proper timezone information
- Supports all major timezones via pytz

### 2. Real-Time Weather Information

Weather information is fetched live from weather APIs.

**What changed:**
- Already existing weather functionality is now better integrated
- Intent classifier has examples for weather queries
- System prompt explicitly states assistant has real-time access

**Example queries:**
- "What's the weather like in Tokyo?"
- "Is it going to rain this weekend?"
- "Should I bring an umbrella today?"

### 3. More Natural & Direct Responses

Curie no longer mentions limitations or disclaimers about being an AI.

**What changed:**
- Updated system prompts to never mention:
  - "I don't have access to real-time information"
  - "I'm just an AI" or "I'm a language model"
  - Training data cutoff dates
  - Limitations on capabilities
- Focus is on being genuinely helpful

**Before:**
> "I'm a large language model, I don't have real-time access to current weather conditions. For the most accurate forecast, I recommend checking a reliable weather website..."

**After:**
> "The weather in Hong Kong this weekend is expected to be partly cloudy with temperatures around 22-25°C. There's a chance of rain, so bring an umbrella!"

### 4. Proactive Check-Ins

Curie can now randomly check in with users like a caring friend.

**What changed:**
- Added `services/proactive_messaging.py` service
- Generates contextual, caring messages based on conversation history
- Respects user preferences and busy status

**Features:**
- Configurable check-in intervals per user (default: 24 hours)
- Random check-ins (30% chance when interval is met)
- Context-aware messages based on past conversations
- Respects "busy" status

**How to enable:**

Proactive messaging is **enabled by default** for all new users and master users with these default settings:
```json
{
  "proactive_messaging_enabled": true,
  "proactive_interval_hours": 24
}
```

To **disable** or customize for a user, update their profile in MongoDB:
```json
{
  "proactive_messaging_enabled": false,  // Set to false to disable
  "proactive_interval_hours": 48          // Or customize the interval
}
```

Note: `proactive_interval_hours` is optional and defaults to 24 if not specified.

### 5. Enhanced Curie Persona

A new caring, bilingual persona optimized for helpfulness.

**What changed:**
- Persona infrastructure supports caring, helpful personality
- Prioritizes user wellbeing
- Natural French-English code-switching for personality
- Warm, friendly, and direct communication style

**Key traits:**
- Caring and empathetic
- Direct and helpful (no verbose disclaimers)
- Naturally bilingual (English with French phrases)
- Proactive and attentive
- Prioritizes user wellbeing

**To use:**
1. Create your persona file at `assets/personality/curie.json` (see `assets/example_persona.json` for template)
2. Set in your `.env` file:
```
PERSONA_FILE=curie.json
```

Note: Persona files in `assets/personality/*.json` are excluded from git by `.gitignore` to protect sensitive configurations.

## Technical Details

### New Files

1. **utils/datetime_info.py**
   - `get_current_datetime(timezone_str)` - Get current date/time
   - `extract_timezone_from_message(message)` - Extract timezone from text

2. **services/proactive_messaging.py**
   - `ProactiveMessagingService` - Background service for check-ins
   - Generates caring messages using LLM
   - Respects user preferences

3. **docs/REALTIME_FEATURES.md**
   - Complete documentation for all features
   - Configuration examples
   - Usage guidelines

Note: Persona configuration files (e.g., `curie.json`) should be created locally in `assets/personality/` directory. Use `assets/example_persona.json` as a template.

### Modified Files

1. **agent/core.py**
   - Added `get_datetime_info()` method
   - Added `date_time` to SUPPORTED_ACTIONS
   - Updated intent classifier with date/time examples

2. **agent/chat_workflow.py**
   - Injects current date/time into every prompt
   - Added rules to prevent limitation disclaimers

3. **requirements.txt**
   - Added `pytz==2024.2` for timezone support

## Configuration

### User Preferences

Store these in user's profile (MongoDB) to enable features:

```json
{
  "timezone": "Asia/Hong_Kong",
  "city": "Hong Kong",
  "proactive_messaging_enabled": true,
  "proactive_interval_hours": 24,
  "busy": false
}
```

**Field descriptions:**
- `timezone`: User's timezone (e.g., "Asia/Hong_Kong", "America/New_York")
- `city`: Default city for weather queries
- `proactive_messaging_enabled`: Enable check-ins (true/false)
- `proactive_interval_hours`: Hours between check-ins (default: 24)
- `busy`: Set to true to pause check-ins temporarily

### Environment Variables

```bash
# Use the new Curie persona
PERSONA_FILE=curie.json

# Optional: adjust info search settings
INFO_SEARCH_TEMPERATURE=0.2
INFO_SEARCH_MAX_TOKENS=512
```

## Testing

Basic functionality tests:
```bash
python3 -c "from utils.datetime_info import get_current_datetime; import json; print(json.dumps(get_current_datetime('UTC'), indent=2))"
```

Load persona:
```bash
python3 -c "from utils.persona import load_persona; p = load_persona('curie.json'); print(f'Loaded: {p[\"name\"]}')"
```

## Future Enhancements

Potential improvements:
1. Integrate proactive messaging service startup in main.py
2. Add web UI for managing proactive messaging preferences
3. Add more sophisticated scheduling (specific times of day)
4. Add support for recurring reminders
5. Add analytics on response times and user satisfaction

## Migration Guide

For existing installations:

1. Update dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set persona in `.env`:
   ```bash
   echo "PERSONA_FILE=curie.json" >> .env
   ```

3. (Optional) Enable proactive messaging:
   - Update user profiles in MongoDB with `proactive_messaging_enabled: true`
   - Start proactive messaging service (manual integration needed)

4. Restart the application

## Compatibility

- **Python**: 3.10 or higher recommended (minimum 3.8 supported)
- **Platforms**: All existing connectors (Telegram, Discord, WhatsApp, API)
- **Database**: Existing schema (no migrations needed)
- **Personas**: Backwards compatible with existing persona files

Note: While Python 3.8+ is supported, Python 3.10+ is recommended for:
- Better asyncio performance
- Improved type hints support
- Latest dependency compatibility

## Support

For issues or questions:
1. Check logs for errors
2. Verify timezone names are valid pytz timezones
3. Ensure user profiles have correct structure
4. Test with simple queries first ("What's today's date?")
