# C.U.R.I.E. — Clever Understanding and Reasoning Intelligent Entity

C.U.R.I.E. is a **local, multi-platform AI assistant** inspired by iconic fictional AIs — **Jarvis & Friday** from Iron Man, **Bagley** from Watch Dogs Legion, **Gideon** from the DC Universe, **HAL 9000** from 2001: A Space Odyssey, **SAM** from Transcendence, **Cortana** from Halo, **EDI** from Mass Effect, **GLaDOS** from Portal, **SHODAN** from System Shock, **Samantha** from Her, **C-3PO & R2-D2** from Star Wars, and **Data** from Star Trek. It runs entirely on your hardware using open GGUF language models — no cloud account required — and integrates with Telegram, Discord, WhatsApp, and a REST/WebSocket API out of the box.

> **Cloud providers are optional.** Anthropic Claude, OpenAI GPT, and Google Gemini can be layered on top for richer responses while keeping your default conversations local and private.

---

## 📱 Supported Platforms

| Platform | Text | Voice | Status |
|----------|------|-------|--------|
| **Telegram** | ✅ | ✅ | Stable |
| **Discord** | ✅ | ✅ | Stable |
| **WhatsApp** | ✅ | ✅ | Beta |
| **REST API** | ✅ | ✅ | Stable |
| **WebSocket** | ✅ | 🔜 | Stable |

---

## ⚡ Quick Start

Get C.U.R.I.E. running in under 10 minutes.

### Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.10 or higher |
| PostgreSQL | Any recent version (or use Docker) |
| MongoDB | Any recent version (or use Docker) |
| GGUF model file | e.g. `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` |
| (Optional) Telegram token | From [@BotFather](https://t.me/botfather) |

---

### Step 1 — Clone & install

```bash
git clone https://github.com/yessur3808/curie-ai.git
cd curie-ai

# Create and activate a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# Install core dependencies
pip install -r requirements.txt   # or: make install

# Optional: voice (Whisper), Discord, and WhatsApp support
# pip install -r requirements-optional.txt   # or: make install-optional

# Verify everything installed correctly
python scripts/verify_setup.py    # or: make verify
```

> **Python 3.13+ note:** `openai-whisper` may fail to build on Python 3.13+. Skip `requirements-optional.txt` if you don't need voice features.

---

### Step 2 — Configure your environment

```bash
cp .env.example .env
# Edit .env with your preferred editor (nano, vim, VS Code, etc.)
```

**Minimum viable `.env` for a Telegram + local-LLM setup:**

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token

# LLM — place the .gguf file inside a models/ directory
LLM_MODELS=Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=assistant_db
POSTGRES_USER=your_pg_user
POSTGRES_PASSWORD=your_pg_password

# MongoDB
MONGODB_URI=mongodb://localhost:27017/
MONGODB_DB=assistant_db

# Your Telegram user ID (grants admin privileges)
MASTER_USER_ID=123456789
```

> See [Environment Variables](#-environment-variables) for the full reference.

---

### Step 3 — Start databases

**Using Docker (recommended):**

```bash
docker-compose up -d postgres mongo   # start containers
make setup-db                         # run migrations + create master user
```

**Manual (if you have PostgreSQL/MongoDB installed locally):**

```bash
python scripts/apply_migrations.py
python scripts/gen_master_id.py
python scripts/insert_master.py
```

---

### Step 4 — Download a GGUF model

Place any compatible `.gguf` file in a `models/` directory (create it if needed):

```bash
mkdir -p models
# Download from HuggingFace, e.g.:
# https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF
```

Set the filename in `.env`:

```env
LLM_MODELS=Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
```

---

### Step 5 — Run C.U.R.I.E.

```bash
# Using Make shortcuts:
make run-telegram   # Telegram only
make run-api        # REST API only (port 8000)
make run-all        # All connectors

# Or run directly:
python main.py --telegram
python main.py --api
python main.py --all
```

You should see:
```
✅ ChatWorkflow initialized with persona: Jarvis
Starting Telegram connector...
```

Open Telegram, find your bot, and say hello!

---

### Step 6 — (Optional) Customize your persona

C.U.R.I.E. ships with several pre-built personas in `assets/personality/`:

| File | Personality |
|---|---|
| `personality.json` | Default polished assistant |
| `jarvis.json` | Formal, tactical, high-precision |
| `friday.json` | Friendly, adaptive, proactive |
| `gideon.json` | Analytical, strategic, context-heavy |
| `bagley.json` | Witty, efficient, slightly sarcastic |
| `curie.json` | Curious, warm, science-inspired |
| `andreja.json` | Calm, thoughtful, diplomatic |

Set the active persona in `.env`:

```env
ASSISTANT_NAME=jarvis
PERSONA_FILE=jarvis.json
```

---

## 📋 Usage Guide

### Talking to C.U.R.I.E.

C.U.R.I.E. understands **natural language** — just type normally. No need to memorize commands for most tasks:

```
You:   Plan a 5-day trip to Tokyo on a moderate budget
You:   Remind me to take my medication in 30 minutes
You:   Convert 250 USD to EUR
You:   Review the function in this code and find any bugs
You:   What is the weather forecast for London?
You:   How many miles is 10 km?
You:   Who was Marie Curie?
```

### Platform-Specific Bot Commands

These slash commands are available on **Telegram** and **Discord**:

| Command | Description |
|---|---|
| `/start` | Display the assistant's greeting |
| `/help` | Show available commands (Discord) |
| `/busy` | Pause proactive messages while you focus |
| `/resume` | Resume normal proactive messaging |
| `/remember <key> <value>` | Save a personal fact (e.g. `/remember city Paris`) |
| `/identify <secret_username>` | Link your platform account to an existing profile |
| `/reset` | Clear your conversation history |
| `/history` | Show recent conversation history |
| `/reminders` | List your upcoming reminders (Telegram) |
| `/clear_memory` | Wipe all stored conversation context (admin) |

### Example Conversations

**Setting a reminder:**
```
You:   Remind me in 2 hours to call the dentist
Bot:   ⏰ Got it! I'll remind you to call the dentist today at 4:30 PM UTC.

You:   List my reminders
Bot:   📋 Your upcoming reminders:
       1. call the dentist — Today at 4:30 PM UTC

You:   Delete reminder 1
Bot:   🗑️ Deleted reminder: call the dentist
```

**Trip planning:**
```
You:   Plan a 3-day luxury trip to Rome
Bot:   ✈️ Trip Plan: Rome (3 Days, Luxury)
       Day 1: Arrive at FCO, check in to a 5-star hotel near the Colosseum...
       ...
       Daily budget estimate (luxury tier): ~$400–600 USD
```

**Currency conversion:**
```
You:   Convert 500 GBP to JPY
Bot:   💱 Currency Conversion:
       500.00 GBP = 96,340.00 JPY
       Exchange rate: 1 GBP = 192.680000 JPY
```

**Unit conversion:**
```
You:   How many kilograms is 180 pounds?
Bot:   ⚖️ Unit Conversion (Mass):
       180.0000 pounds = 81.6466 kilograms
```

---

## 🤖 AI Assistant Capabilities

### What C.U.R.I.E. Can Do

| Category | Capability |
|---|---|
| **Conversation** | Context-aware chat with long-term memory and session history |
| **Reminders** | Natural-language reminder setting, listing, and deletion |
| **Trip Planning** | Day-by-day itineraries, packing lists, budget estimates |
| **Conversions** | Real-time currency exchange (150+ currencies) and unit conversions |
| **Web Search** | DuckDuckGo-powered information search and summarization |
| **Navigation** | Route planning and location-based queries |
| **Coding Assistant** | Code generation, review, bug detection, performance analysis |
| **PR/MR Management** | Create and review GitHub, GitLab, and Bitbucket pull requests |
| **Pair Programming** | Interactive collaborative coding sessions |
| **Voice** | Speech-to-text (Whisper) and text-to-speech (Google TTS) |
| **Proactive Messaging** | Scheduled background messages and reminder delivery |
| **Persona** | Fully customizable personality via JSON |

---

### Best Uses

- **Personal productivity**: Reminders, trip planning, quick conversions, Q&A
- **Developer workflows**: Code review, PR management, pair programming, bug detection across GitHub/GitLab/Bitbucket
- **Research**: Web search summarization, fact lookup, topic exploration
- **Privacy-first AI**: Runs fully locally — your conversations never leave your machine
- **Multi-device access**: Same bot accessible on Telegram, Discord, WhatsApp, and via API from any app or script

---

### Benefits

- **100% local by default** — no data sent to external servers unless you configure cloud providers
- **Multi-platform** — one backend powers Telegram, Discord, WhatsApp, REST API, and WebSocket simultaneously
- **Persistent memory** — remembers your preferences, past conversations, and learned facts across sessions
- **Modular & extensible** — skills are independent Python modules; add new capabilities without touching core logic
- **Production-ready** — PM2/systemd support, response deduplication, LLM response caching, graceful fallback
- **Flexible LLM routing** — use local models for simple queries and cloud providers for complex ones to control costs

---

## 📱 Supported Platforms

| Platform | Text | Voice | Status | Notes |
|---|---|---|---|---|
| **Telegram** | ✅ | ✅ | Stable | Full command set, Markdown rendering |
| **Discord** | ✅ | ✅ | Stable | Long messages auto-chunked, full Markdown |
| **WhatsApp** | ✅ | ✅ | Beta | Markdown stripped for plain-text rendering |
| **REST API** | ✅ | ✅ | Stable | FastAPI on port 8000, idempotency support |
| **WebSocket** | ✅ | — | Stable | Real-time bidirectional chat |

---

## 🌐 REST API Reference

The FastAPI server runs on **port 8000** by default.

### `POST /chat`

Send a message and receive a response.

```json
// Request
{
  "user_id": "user123",
  "message": "What is the capital of France?",
  "idempotency_key": "optional-uuid-v4",
  "voice_response": false,
  "username": "optional_handle"
}

// Response
{
  "text": "The capital of France is Paris.",
  "timestamp": "2026-03-23T17:00:00.000Z",
  "model_used": "Meta-Llama-3.1-8B",
  "processing_time_ms": 420,
  "voice_url": null
}
```

### `GET /health`

Check service status and cache statistics.

```json
{
  "status": "healthy",
  "workflow_initialized": true,
  "cache_stats": {
    "prompt_cache": { "hits": 42, "misses": 58, "hit_rate_percent": 42.0 }
  }
}
```

### `GET /reminders?user_id=<id>`

List upcoming reminders for a user.

### `DELETE /reminders?user_id=<id>[&index=<n>]`

Delete one reminder (by 1-based index) or all reminders for a user.

### `POST /transcribe`

Transcribe an audio file to text.

```
Form fields:
  file        — audio file (mp3, wav, ogg, m4a, flac, opus; max 25 MB)
  user_id     — optional user ID
  language    — language code (default: en)
  accent      — optional accent hint
```

### `GET /audio/{filename}`

Stream a previously generated voice response file.

### `POST /clear_memory`

Clear all conversation history for a user (admin only).

```json
{ "user_id": "user123" }
```

### `WebSocket /ws/chat`

Real-time bidirectional chat.

```json
// Client sends:
{ "user_id": "user123", "message": "Hello!" }

// Server responds:
{
  "text": "Hello! How can I help?",
  "timestamp": "2026-03-23T17:00:00Z",
  "model_used": "Meta-Llama-3.1-8B",
  "processing_time_ms": 390
}
```

---

## 🔗 Integrations

### Platform Connectors

| Connector | Enable Flag | Required Credential |
|---|---|---|
| Telegram | `RUN_TELEGRAM=true` | `TELEGRAM_BOT_TOKEN` |
| Discord | `RUN_DISCORD=true` | `DISCORD_BOT_TOKEN` |
| WhatsApp | `RUN_WHATSAPP=true` | `WHATSAPP_SESSION_PATH` |
| REST/WebSocket API | `RUN_API=true` | *(none)* |

### LLM Providers

| Provider | Priority Key | Required Variable |
|---|---|---|
| llama.cpp (local) | `llama.cpp` | `LLM_MODELS` |
| Anthropic Claude | `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI GPT | `openai` | `OPENAI_API_KEY` |
| Google Gemini | `gemini` | `GOOGLE_API_KEY` |

Configure routing order with:

```env
LLM_PROVIDER_PRIORITY=anthropic,openai,gemini,llama.cpp
```

Simple queries are automatically routed to the local model (cost optimization) unless `LLM_CLOUD_SIMPLE_TASKS=true`.

### Code Repository Integrations

| Platform | Required Variables |
|---|---|
| **GitHub** | `GITHUB_TOKEN`, `MAIN_REPO`, `MAIN_REVIEWER`, `TARGET_BRANCH` |
| **GitLab** | `GITLAB_TOKEN`, `GITLAB_URL` |
| **Bitbucket** | `BITBUCKET_USERNAME`, `BITBUCKET_APP_PASSWORD` |

---

## ✅ Current Features

### Conversational AI
- Context-aware chat with per-user, per-channel session history
- Long-conversation auto-summarisation (configurable threshold)
- Proactive learning: automatically extracts and stores user preferences from conversation
- Persistent user profiles backed by PostgreSQL and MongoDB

### Reminders & Scheduling
- Natural-language reminder creation: *"remind me in 30 minutes to take my pills"*
- Flexible time formats: `in N minutes/hours/days`, `at 3pm`, `tomorrow at 10am`, ISO dates
- Background delivery via the proactive messaging service
- List and delete via chat or REST API

### Trip & Vacation Planning
- Full day-by-day itineraries from natural language requests
- Packing list generation
- Budget tier estimates (budget / moderate / luxury)

### Currency & Unit Conversions
- Live currency exchange for 150+ currencies
- Unit conversions: length, mass, volume, temperature, speed, area
- Natural-language queries understood on all platforms

### Voice Interface
- **Speech-to-text**: OpenAI Whisper with automatic language detection
- **Text-to-speech**: Google TTS with multi-accent support (American, British, Indian, Australian, and more)
- Persona-based voice settings (accent, language, speed)

### Advanced Coding Suite
- **Code generation**: Multi-language AI code creation
- **Code review**: AI-powered review with detailed feedback
- **Bug detection**: Static-analysis pattern matching for common bugs and security vulnerabilities
- **Proactive bug scanning**: Continuous directory monitoring
- **Performance analysis**: Big O estimation, bottleneck detection, optimization suggestions
- **Pair programming**: Real-time collaborative coding sessions with context tracking
- **PR/MR management**: Create, review, and manage pull/merge requests on GitHub, GitLab, Bitbucket
- **Self-update**: Safe auto-update mechanism with rollback capability
- **Standalone coding service**: Run code operations independently in parallel (`RUN_CODING_SERVICE=true`)

### Web Search & Information
- DuckDuckGo-powered search and AI summarization
- Configurable result count and snippet length

### Navigation
- Route planning and travel time queries
- Location-based context

### Multi-Provider LLM
- Local GGUF models via llama.cpp (no internet required)
- Optional cloud providers: Anthropic, OpenAI, Google Gemini
- Automatic routing: simple queries → local, complex queries → cloud
- Response caching with TTL to reduce redundant LLM calls

### Persona System
- Seven built-in personalities (jarvis, friday, gideon, bagley, curie, andreja, default)
- Fully customizable via JSON (name, greeting, tone, constraints, voice settings)
- Multi-persona mode: set `PERSONA_FILE=all` to load all personas

### Proactive Messaging
- Background service delivers reminders and scheduled messages
- Per-user contact channel preferences (platform priority, blocked platforms)
- Configurable check interval (`PROACTIVE_CHECK_INTERVAL`)

### Deployment Options
- **Direct**: `python main.py [--telegram] [--discord] [--api] [--all]`
- **Make shortcuts**: `make run-telegram`, `make run-api`, `make run-all`
- **PM2**: `pm2 start ecosystem.config.js`
- **systemd**: service file configurable via `SYSTEMD_SERVICE_NAME`
- **Docker Compose**: databases provisioned automatically

---

## 🚀 Upcoming Features

See [docs/FEATURE_ROADMAP.md](docs/FEATURE_ROADMAP.md) for detailed implementation plans.

### Priority 1 — High Impact, Lower Complexity
- **Enhanced News Analysis** — aggregation from multiple sources, sentiment analysis, trending topics
- **Basic Financial Data** — cryptocurrency prices, stock quotes, Forex rates, market status (view-only)

### Priority 2 — Medium Complexity
- **Email Integration** — send/receive via SMTP or API (SendGrid, Mailgun), scheduling, templates
- **Nutrition & Wellness** — nutrition database lookup, calorie/macro tracking, health calculators

### Priority 3 — More Complex
- **Legal & Tax Reference** — US tax brackets, deduction lookup, basic legal definitions *(with disclaimers — not professional advice)*
- **Advanced Financial Analysis** — technical indicators (RSI, MACD), portfolio tracking, educational backtesting

### Platform & Infrastructure
- **Web Dashboard / UI** — browser-based chat and management interface
- **Advanced Memory Management** — cross-session entity tracking and long-term knowledge graph
- **Enhanced Multi-User Support** — shared spaces, group conversation context
- **Plugin System** — third-party skill packages

> ⚠️ Features requiring broker partnerships, medical diagnosis, unauthorized legal practice, or tax filing services are **not planned**.

---

## 🔧 Environment Variables

Copy `.env.example` to `.env` and configure these variables.

### Core / Required

| Variable | Description | Example |
|---|---|---|
| `MASTER_USER_ID` | User ID with admin privileges | `123456789` |
| `POSTGRES_HOST` | PostgreSQL host | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_DB` | PostgreSQL database name | `assistant_db` |
| `POSTGRES_USER` | PostgreSQL username | `your_pg_user` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `your_pg_password` |
| `MONGODB_URI` | MongoDB connection URI | `mongodb://localhost:27017/` |
| `MONGODB_DB` | MongoDB database name | `assistant_db` |

### Platform Connector Tokens

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from [@BotFather](https://t.me/botfather) |
| `DISCORD_BOT_TOKEN` | Discord bot token from the Developer Portal |
| `WHATSAPP_SESSION_PATH` | Directory to persist the WhatsApp session |

### Connector Enable Flags

| Variable | Default | Description |
|---|---|---|
| `RUN_TELEGRAM` | `true` | Enable Telegram connector |
| `RUN_DISCORD` | `false` | Enable Discord connector |
| `RUN_WHATSAPP` | `false` | Enable WhatsApp connector |
| `RUN_API` | `true` | Enable REST/WebSocket API (port 8000) |
| `RUN_CODER` | `false` | Enable interactive coder mode |
| `RUN_CODING_SERVICE` | `false` | Enable standalone coding service |

### LLM Configuration

| Variable | Default | Description |
|---|---|---|
| `LLM_MODELS` | *(required)* | Comma-separated GGUF filenames in `models/` |
| `CODING_MODEL_NAME` | *(none)* | Dedicated GGUF model for coding tasks |
| `LLM_PROVIDER_PRIORITY` | `llama.cpp` | Provider order, e.g. `anthropic,openai,llama.cpp` |
| `LLM_CLOUD_SIMPLE_TASKS` | `false` | Route simple queries to cloud (increases cost) |
| `LLM_CONTEXT_SIZE` | `2048` | Context window size in tokens |
| `LLM_DEFAULT_MAX_TOKENS` | `256` | Default max tokens per response |
| `OPENAI_API_KEY` | *(none)* | OpenAI API key (optional) |
| `OPENAI_MODEL` | `gpt-3.5-turbo` | OpenAI model name |
| `ANTHROPIC_API_KEY` | *(none)* | Anthropic API key (optional) |
| `ANTHROPIC_MODEL` | `claude-3-haiku-20240307` | Anthropic model name |
| `GOOGLE_API_KEY` | *(none)* | Google Gemini API key (optional) |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini model name |

### Persona & Behavior

| Variable | Default | Description |
|---|---|---|
| `ASSISTANT_NAME` | `jarvis` | Display name (used for speaker-tag removal) |
| `PERSONA_FILE` | `personality.json` | Persona JSON filename, or `all` for multi-persona |
| `MINIMAL_SANITIZATION` | `true` | Natural chat output; set `false` for aggressive filtering |
| `ENABLE_PROACTIVE_MESSAGING` | `true` | Enable background reminder/message delivery |
| `PROACTIVE_CHECK_INTERVAL` | `3600` | Background check frequency in seconds |
| `ENABLE_LEARNING` | `true` | Auto-extract user preferences from conversations |
| `LEARNING_MAX_FACTS` | `50` | Max stored facts per user |

### Code Repository Integrations

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub personal access token |
| `MAIN_REPO` | Default repository URL |
| `MAIN_REVIEWER` | Default reviewer username |
| `TARGET_BRANCH` | Default target branch (e.g. `main`) |
| `GITLAB_TOKEN` | GitLab personal access token |
| `GITLAB_URL` | GitLab instance URL (default: `https://gitlab.com`) |
| `BITBUCKET_USERNAME` | Bitbucket username |
| `BITBUCKET_APP_PASSWORD` | Bitbucket app password |

### Advanced / Optional

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_TIMEZONE` | `UTC` | Fallback timezone for date/time queries |
| `DEFAULT_LOCATION` | *(none)* | Fallback location for location-based queries |
| `WHISPER_MODEL` | `base` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` |
| `SESSION_SCOPE` | `per_channel_user` | Session isolation: `single`, `per_user`, `per_channel_user` |
| `SESSION_MAX_HISTORY` | `50` | Max messages retained per session |
| `HISTORY_SUMMARISE_THRESHOLD` | `20` | Compress history after this many turns |
| `HISTORY_KEEP_RECENT` | `6` | Verbatim recent turns to keep after summarisation |
| `PROJECTS_ROOT` | *(none)* | Root directory for project management |
| `SYSTEMD_SERVICE_NAME` | *(none)* | Systemd service name for self-update restarts |

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     MESSAGING PLATFORMS                      │
│   Telegram   │   Discord   │   WhatsApp   │   REST/WS API   │
└──────────────┴─────────────┴──────────────┴─────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │   ChatWorkflow     │  ← central message router
                    │  (chat_workflow.py)│
                    └────────┬───────────┘
           ┌─────────────────┼──────────────────────┐
           ▼                 ▼                      ▼
   ┌───────────────┐  ┌─────────────┐      ┌────────────────┐
   │  Agent Skills │  │   Memory    │      │  LLM Providers │
   │  • scheduler  │  │ PostgreSQL  │      │  • llama.cpp   │
   │  • trip_plan  │  │  MongoDB    │      │  • Anthropic   │
   │  • coding     │  │  Sessions   │      │  • OpenAI      │
   │  • navigator  │  │  Learning   │      │  • Gemini      │
   │  • find_info  │  └─────────────┘      └────────────────┘
   │  • convert    │
   └───────────────┘
           │
   ┌───────▼────────┐
   │  Proactive Svc │  ← background: reminders, scheduled msgs
   └────────────────┘
```

---

## 📁 Project Structure

See [directory_structure.md](./directory_structure.md) for a full file listing.

```
curie-ai/
├── agent/                  # Core agent logic
│   ├── chat_workflow.py    # Unified message processing pipeline
│   ├── core.py             # Agent class, conversation handling
│   └── skills/             # Skill modules (scheduler, coding, trips, etc.)
├── connectors/             # Platform integrations
│   ├── telegram.py
│   ├── discord_bot.py
│   ├── whatsapp.py
│   └── api.py              # FastAPI REST + WebSocket server
├── llm/                    # LLM management
│   ├── manager.py          # Model loading, caching
│   └── providers.py        # Multi-provider abstraction
├── memory/                 # Data persistence
│   ├── database.py         # PostgreSQL connection & migrations
│   ├── users.py            # User management
│   ├── session_manager.py  # Session handling
│   └── learning.py         # Proactive preference extraction
├── services/               # Background services
│   ├── proactive_messaging.py
│   └── cron_runner.py
├── utils/                  # Utility modules (voice, formatting, time, etc.)
├── assets/personality/     # Persona JSON files
├── migrations/             # Database schema versioning
├── scripts/                # Setup and utility scripts
├── docs/                   # Extended documentation
├── main.py                 # Entry point
├── .env.example            # Environment variable reference
├── docker-compose.yml      # PostgreSQL + MongoDB containers
└── ecosystem.config.js     # PM2 process configuration
```

---

## 📚 Documentation

| Guide | Description |
|---|---|
| [Quick Start](docs/QUICK_START.md) | Detailed 5-minute setup walkthrough |
| [Multi-Platform Guide](docs/MULTI_PLATFORM_GUIDE.md) | Platform-specific setup and voice configuration |
| [Advanced Coding Features](docs/ADVANCED_CODING_FEATURES.md) | Pair programming, bug detection, performance analysis |
| [Coding Modules Guide](docs/CODING_MODULES_GUIDE.md) | Code review, PR management, self-update |
| [Troubleshooting Guide](docs/TROUBLESHOOTING.md) | Common errors and fixes |
| [Quick Reference](docs/QUICK_REFERENCE.md) | Commands, API usage, and developer integration |
| [Feature Roadmap](docs/FEATURE_ROADMAP.md) | Planned features with implementation details |
| [PM2 Setup](docs/PM2_SETUP.md) | Production process management |
| [Migration Guide](docs/MIGRATION_GUIDE.md) | Upgrading between versions |

---

## 🛠️ Development

```bash
make test          # Run all tests
make lint          # Lint with flake8
make format        # Format with black
make check         # Lint + format check (non-destructive)
make check-ports   # Verify required ports are available
make clean         # Remove Python cache files
```

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a pull request.

## 📄 License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

---

**C.U.R.I.E. — Your Personal AI Assistant, Running Locally.**
