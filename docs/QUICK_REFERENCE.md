# Quick Reference — C.U.R.I.E. AI

A compact cheat-sheet for common commands, API calls, and configuration.

---

## Bot Commands

These slash commands are available on **Telegram** and **Discord**.

| Command | Description |
|---|---|
| `/start` | Display the assistant's greeting |
| `/help` | Show available commands (Discord) |
| `/busy` | Pause proactive messages while you focus |
| `/resume` | Resume normal proactive messaging |
| `/remember <key> <value>` | Save a personal fact — e.g. `/remember city Paris` |
| `/identify <secret_username>` | Link your platform account to an existing internal profile |
| `/reset` | Clear your conversation history |
| `/history` | Show recent conversation history |
| `/reminders` | List your upcoming reminders (Telegram) |
| `/clear_memory` | Wipe all stored conversation context (admin only) |

---

## Natural Language Examples

C.U.R.I.E. understands plain language — no commands needed for most tasks.

### Reminders
```
Remind me in 30 minutes to take my medication
Remind me tomorrow at 9am to call the bank
List my reminders
Delete reminder 1
Cancel all reminders
```

**Supported time formats:** `in N minutes/hours/days/weeks`, `at 3pm`, `at 14:30`,
`tomorrow`, `tomorrow at 10am`, ISO date (`2026-06-15`)

### Trip Planning
```
Plan a 5-day budget trip to Barcelona
Plan a luxury weekend in New York
What should I pack for a beach vacation?
Give me a packing list for a ski trip
```

**Budget tiers:** budget / moderate / luxury

### Currency & Unit Conversions
```
Convert 100 USD to EUR
How many miles is 10 km?
What is 25 celsius in fahrenheit?
Convert 5 kg to pounds
How many liters in 2 gallons?
```

**Supported unit categories:** length, mass, volume, temperature, speed, area
**Currencies:** 150+ codes (USD, EUR, GBP, JPY, CAD, AUD, …)

### Coding
```
Review this function for bugs: [paste code]
Generate a Python function that sorts a list of dicts by key
What is the Big O complexity of this algorithm?
Help me optimize this SQL query
Create a GitHub PR for my recent changes
```

### General
```
Search for the latest news about AI regulation
What time is it in Tokyo?
Who was Marie Curie?
What is the weather forecast for London?
```

---

## REST API Quick Reference

Base URL: `http://localhost:8000`

### Send a message

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user123", "message": "Hello!"}'
```

### Health check

```bash
curl http://localhost:8000/health
```

### List reminders

```bash
curl "http://localhost:8000/reminders?user_id=user123"
```

### Delete all reminders

```bash
curl -X DELETE "http://localhost:8000/reminders?user_id=user123"
```

### Delete a specific reminder (1-based index)

```bash
curl -X DELETE "http://localhost:8000/reminders?user_id=user123&index=1"
```

### Transcribe audio

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@recording.mp3" \
  -F "user_id=user123" \
  -F "language=en"
```

### Clear memory (admin only)

```bash
curl -X POST http://localhost:8000/clear_memory \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user123"}'
```

### WebSocket chat

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/chat");
ws.onopen = () => ws.send(JSON.stringify({ user_id: "user123", message: "Hi!" }));
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

---

## Make Commands

```bash
# Installation
make install              # Install core dependencies
make install-optional     # Install optional deps (voice, Discord, WhatsApp)
make verify               # Verify setup

# Database
make db-start             # Start PostgreSQL + MongoDB via Docker
make db-stop              # Stop database containers
make db-restart           # Restart database containers
make db-status            # Show container status
make setup-db             # Run migrations + create master user

# Running the application
make run                  # Start with default .env connector flags
make run-telegram         # Telegram connector only
make run-discord          # Discord connector only
make run-whatsapp         # WhatsApp connector only
make run-api              # REST/WebSocket API only (port 8000)
make run-all              # All connectors

# Development
make test                 # Run tests with pytest
make lint                 # Lint with flake8
make format               # Format with black
make check                # Lint + format-check (non-destructive)
make check-ports          # Check if required ports are available
make test-imports         # Test all imports
make clean                # Remove Python cache files

# Migrations
make migrate              # Apply all SQL migrations
make migrate-down         # Revert all migrations (destructive!)
```

---

## CLI Launch Options

```bash
python main.py --telegram        # Telegram only
python main.py --discord         # Discord only
python main.py --whatsapp        # WhatsApp only
python main.py --api             # REST API only
python main.py --all             # All connectors
python main.py --coder           # Interactive coder mode
python main.py --coder-batch     # Batch coder mode (requires --coder-config)
python main.py --coding-service  # Standalone coding service
```

---

## PM2 Commands

```bash
pm2 start ecosystem.config.js    # Start all processes
pm2 stop curie-main              # Stop main process
pm2 restart curie-main           # Restart main process
pm2 logs curie-main              # View live logs
pm2 logs curie-main --lines 100  # View last 100 log lines
pm2 status                       # Show process status
pm2 monit                        # Interactive monitoring dashboard
```

---

## Key Environment Variables

```env
# Connector tokens
TELEGRAM_BOT_TOKEN=your_token
DISCORD_BOT_TOKEN=your_token

# Enable/disable connectors
RUN_TELEGRAM=true
RUN_DISCORD=false
RUN_API=true

# LLM
LLM_MODELS=Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
LLM_PROVIDER_PRIORITY=llama.cpp               # or: anthropic,openai,llama.cpp
LLM_CONTEXT_SIZE=4096

# Cloud providers (optional)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...

# Databases
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=assistant_db
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
MONGODB_URI=mongodb://localhost:27017/
MONGODB_DB=assistant_db

# Persona
ASSISTANT_NAME=jarvis
PERSONA_FILE=jarvis.json         # or: friday.json, gideon.json, bagley.json

# Admin (master account)
# MASTER_USER_ID is the internal user UUID for the master account (not a Telegram/Discord numeric ID).
MASTER_USER_ID=00000000-0000-0000-0000-000000000000
# If bootstrapping via scripts/insert_master.py, also set:
# MASTER_TELEGRAM_ID=your_telegram_numeric_id
# MASTER_SECRET_USERNAME=your_secret_link_code
```

---

## Persona JSON Structure

```json
{
  "name": "Jarvis",
  "description": "Formal, tactical, high-precision assistant",
  "system_prompt": "You are Jarvis, a highly capable AI assistant...",
  "greeting": "Good day. How may I assist you?",
  "response_style": {
    "brevity": "concise",
    "tone": "professional",
    "humor": "subtle",
    "formality": "formal",
    "clarity": "prioritized"
  },
  "constraints": ["never make up facts", "always cite sources when possible"],
  "personality": {
    "traits": ["precise", "analytical", "composed"]
  },
  "voice": {
    "language": "en",
    "accent": "en-GB",
    "speed": 1.0
  }
}
```

---

## Troubleshooting Quick Fixes

| Problem | Fix |
|---|---|
| Bot not responding | Verify token in `.env`, check no other instance is running |
| Database connection error | Run `docker-compose ps`, verify `.env` credentials |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` or `make install` |
| `openai-whisper` build fails | Skip `requirements-optional.txt` (Python 3.13+ issue, non-critical) |
| Slow responses | Check `GET /health` cache stats; consider increasing `LLM_CONTEXT_SIZE` |
| Duplicate responses | Idempotency cache handles this automatically; check dedup cache size |

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed solutions.

---

**Version**: Current  
**Last Updated**: March 2026
