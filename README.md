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

For a system-wide user command that works from any directory, run the installer:

```bash
./install.sh --no-onboard
curie dashboard
```

The installer places the launcher in `~/.local/bin/curie`. Ensure
`~/.local/bin` is included in your `PATH` if your shell does not already include it.

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

# Master user UUID used for admin privileges (generate with: python scripts/gen_master_id.py)
MASTER_USER_ID=550e8400-e29b-41d4-a716-446655440000
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

# Required for scripts/insert_master.py:
export MASTER_TELEGRAM_ID="<your_telegram_user_id>"        # numeric Telegram user ID
export MASTER_SECRET_USERNAME="<your_secret_username>"     # internal handle for the master user

# Generate a master ID and write it into .env
python scripts/gen_master_id.py --env

# Insert the master user into the database
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
✅ ChatWorkflow initialized with persona: Sentinel
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

Telegram uses `/` prefix; Discord uses the `!` prefix (e.g. `!start`, `!help`).

**Telegram (`/`)**

| Command | Description |
|---|---|
| `/start` | Display the assistant's greeting |
| `/busy` | Pause proactive messages while you focus |
| `/resume` | Resume normal proactive messaging |
| `/remember <key> <value>` | Save a personal fact (e.g. `/remember city Paris`) |
| `/identify <secret_username>` | Link your platform account to an existing profile |
| `/reset` | Clear your conversation history |
| `/history` | Show recent conversation history |
| `/reminders` | List your upcoming reminders |
| `/clear_memory` | Wipe all stored conversation context (admin) |
| `/voice on\|off\|status` | Enable, disable, or inspect Telegram voice replies |
| `/voice_profile clear\|soft\|expressive\|french\|custom` | Select a voice preset |
| `/voice_accent neutral\|subtle\|strong` | Control English/French pronunciation routing |
| `/voice_speed slow\|normal\|fast` | Adjust speech speed |
| `/voice_warmth neutral\|gentle\|warm` | Adjust pauses and vocal softness |
| `/voice_expression calm\|balanced\|expressive` | Adjust synthesis variation |
| `/voice_sample [text]` | Generate a one-shot sample without changing reply mode |
| `/voice_custom status\|consent\|revoke\|enroll` | Manage consent-gated custom voice enrollment |
| `/voice_help` | Show Curie's voice command guide |

**Discord (`!`)**

| Command | Description |
|---|---|
| `!start` | Display the assistant's greeting |
| `!help` | Show available commands |
| `!busy` | Pause proactive messages while you focus |
| `!resume` | Resume normal proactive messaging |
| `!remember <key> <value>` | Save a personal fact (e.g. `!remember city Paris`) |
| `!identify <secret_username>` | Link your platform account to an existing profile |
| `!reset` | Clear your conversation history |
| `!history` | Show recent conversation history |
| `!clear_memory` | Wipe all stored conversation context (admin) |

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
| **Voice** | Local speech-to-text plus Curie's trained Chatterbox Nano/LoRA voice |
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

### Smart-home providers

Curie can summarize and control Tapo, Nanoleaf, LG ThinQ air devices,
SmartThings, Petlibro (through Home Assistant), Mi Home, and Govee. Provider
credentials, LAN device entries, optional dependencies, and command examples
are documented in [docs/SMART_HOME_INTEGRATIONS.md](docs/SMART_HOME_INTEGRATIONS.md).

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
- **Speech-to-text**: local Whisper-compatible transcription with automatic language detection
- **Text-to-speech**: Curie's locally trained Chatterbox Nano/LoRA voice on bot connectors, the API, and the dashboard
- Persona-aware delivery settings (contextual pace, pauses, warmth, and expression)
- Text-only failure behavior: a worker failure never silently switches Curie back to a different voice

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
- Five built-in personalities (jarvis, friday, gideon, bagley, default)
- Fully customizable via JSON (name, greeting, tone, constraints, voice settings)
- Multi-persona mode: set `PERSONA_FILE=all` to load all personas

### Proactive Messaging
- Background service delivers reminders and scheduled messages
- Per-user contact channel preferences (platform priority, blocked platforms)
- Configurable check interval (`PROACTIVE_CHECK_INTERVAL`)

### Provenance-aware memory controls

In chat, use `/memory inspect`, `/memory why <key>`,
`/memory correct <key> = <value>`, `/memory confirm <id>`,
`/memory forget <key|all>`, `/memory export`, `/memory pause`, or
`/memory resume`. Add “do not remember this” to any message to prevent memory
storage for that turn. Curie rejects credentials and similarly sensitive values
instead of storing them.

### Typed learned skills

Teach a declarative response with “When I say …, …”, then inspect and explicitly
approve the version before it activates. Manage versions in chat with
`/skill inspect <name>`, `/skill disable <name>`, `/skill rollback <name>`,
`/skill feedback <name> <feedback>`, `/skill archive <name>`,
`/skill export [name]`, and `/skill delete <name>`. Executable learned workflows
are limited to registered tools and never retain approval for future mutations.

### Feedback-driven adaptation

Curie records direct presentation feedback such as “too long,” “too short,” or
“use a professional tone.” Inspect the versioned preference history with
`/adaptation`, change bounded settings with `/adaptation set <setting> <value>`,
temporarily use `/adaptation pause`, or clear one/all preferences with
`/reset_preferences [setting]`. Implicit operational signals require repeated
evidence and cannot modify identity, safety rules, permissions, or authority.

### Proactive message controls

Proactive messages are off for new users until explicitly enabled. Use
`/proactive` to inspect the current timezone, quiet hours, daily and weekly
limits, topic cooldown, and snooze state. Use `/proactive enable`,
`/proactive disable`, `/proactive snooze 1h|8h|1d|1w`, or `/proactive why` to
control delivery and see a privacy-safe explanation for the latest message.
Predicted help is suggestion-only and always asks permission before work begins.

### Unified request routing

Every conversational request produces one explainable routing decision with its
confidence, selected capability, live-data requirement, and risk. Independent
requests in one message receive a focused ordering question. Low-confidence
requests remain normal conversation, and model-assisted routing cannot authorize
filesystem or other mutating actions.

### Durable multi-step tasks

Complex work can use persisted dependency graphs with bounded parallel reads,
deadlines, safe retries, cancellation, explicit completion checks, and retained
evidence. Mutating steps pause for a single-use, owner- and step-scoped approval;
an interrupted mutation is verified after restart and is never replayed
automatically. Use `/task inspect <task-id>`, `/task resume <task-id>`,
`/task cancel <task-id>`, or `/task approve <task-id> <approval-token>` in chat.
Use `/task` to list retained tasks. Completed tasks return a receipt naming
affected resources, verification evidence, and the available recovery path.

### Security and retention controls

Every attachment passes executable denial, archive traversal/expansion checks,
and ClamAV when it is installed. Set `CURIE_REQUIRE_MALWARE_SCANNER=true` to
reject media if ClamAV is unavailable. Use `/security` for active controls,
`/privacy retention` for effective retention limits, and `/privacy purge` for
owner-scoped cleanup. The trust boundaries are documented in
[the threat model](docs/THREAT_MODEL.md).

### Managed inference

Conversation model work is coordinated through one bounded, process-wide
priority service. Active messages outrank background inference, superseded
requests can be cancelled, and overload is rejected rather than growing memory
without limit. The dashboard reports managed queue depth/capacity, first-token
latency, throughput, loaded models, and model reloads. Automatic ensembles only
activate when `LLM_ENSEMBLE_EVAL_GAIN` meets `LLM_ENSEMBLE_MIN_GAIN`; explicit
requests for multiple perspectives remain supported.

### Runtime readiness and recovery

Use `/health` in chat or `GET /health` through the API to inspect text, vision,
transcription, speech, database, disk, security, and inference readiness
independently. Optional capability failures degrade to text, a retry request, or
a clear unavailable response instead of silence. Connector, inference, and
media concurrency are bounded so expensive attachments cannot consume every
chat resource.

Local SQLite recovery uses `memory.backup.create_backup()` and
`memory.backup.restore_backup()`. Backups are online-consistent, checksummed,
schema/integrity checked, permissioned `0600`, and restored atomically while
retaining a pre-restore rollback copy. Keep backup encryption keys outside the
backup location.

```bash
python -m scripts.memory_recovery backup backups/curie.sqlite3
python -m scripts.memory_recovery verify backups/curie.sqlite3 --sha256 DIGEST
python -m scripts.memory_recovery restore backups/curie.sqlite3 --sha256 DIGEST --confirm
```

### Public contracts and capability discovery

`contracts/catalog.py` is the versioned machine-readable contract for
connectors, tools, media, memory, voice, and response policy. Use
`/capabilities` or `GET /capabilities` to see only features whose runtime probes
currently pass; add `all` in chat or `include_unavailable=true` through the API
for diagnostics. New integrations must pass the shared conformance suite.

Significant releases are described by `release/current.json`. CI requires its
changelog, evaluation delta, migration notes, and rollback procedure. Applied
PostgreSQL migrations are checksummed and cannot be silently edited; rollback
always requires an explicit target version. See
[the release process](docs/RELEASE_PROCESS.md).

### Cache and index policy

Public lookup caches and owner-scoped personalized caches are TTL-bound,
size-bound, and expose privacy-safe hit-rate metrics through the health API.
Personal model responses require an explicit owner scope. Project indexes
invalidate whenever indexed paths, sizes, or modification times change and omit
known credential files. See [the cache policy](docs/CACHE_POLICY.md) for the
complete inventory and invalidation rules.

### Filesystem and command sandbox

Project tools use canonical owner-scoped roots and deny traversal, link escapes,
credential files, unbounded archives, and oversized project inputs. Approved
edits are applied atomically with a diff and retained rollback copies. Test and
development commands use argument arrays inside Bubblewrap with no network,
filtered environment variables, bounded CPU/memory/process/output/time, and
whole-process-group termination. Commands outside the safe read-only test
profile require fresh scoped approval.

### Secure live research

Research validates every resolved address and redirect, blocks internal and
metadata targets, streams responses under strict size/type/redirect limits, and
removes executable or hidden markup. Web content is passed to synthesis only as
untrusted evidence, never instructions. Results distinguish model synthesis from
source passages, bind citations to retained evidence, and include live-fetch
timestamps. The read-only browser does not submit POST forms.

### Deployment Options
- **Direct**: `python main.py [--telegram] [--discord] [--api] [--all]`
- **Make shortcuts**: `make run-telegram`, `make run-api`, `make run-all`
- **PM2**: `pm2 start ecosystem.config.js`
- **systemd**: service file configurable via `SYSTEMD_SERVICE_NAME`
- **Docker Compose**: databases provisioned automatically

---

## 🚀 Upcoming Features

See the [Curie Enhancement Roadmap](docs/ENHANCEMENT_ROADMAP.md) for the canonical
phased plan covering accuracy, multimodal support, local voice, memory,
friendship, bounded proactivity, personal operations, security, and resilience.

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
| `LLM_CODING_MODEL` | general model | Coding model selected by automatic conversation routing |
| `LLM_GENERAL_MODEL` | first model | Primary conversation/synthesis model |
| `LLM_FAST_MODEL` | general model | Smaller independent critic/fast-task model |
| `LLM_REASONING_MODEL` | general model | Model selected by reasoning specialists |
| `LLM_AGENT_MODEL` | reasoning model | Model used to plan approved project/tool changes |
| `LLM_CRITIC_MODEL` | general model | Independent second-opinion model used by ensembles |
| `LLM_MAX_LOADED_MODELS` | `2` | LRU limit for resident GGUF models |
| `LLM_PARALLEL_WORKERS` | `2` | Maximum concurrent local specialists |
| `LLM_ENSEMBLE_ENABLED` | `true` | Enable explicit multi-agent requests |
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

Multi-agent inference is opt-in per request to preserve normal chat latency. Ask
Curie to “use several agents,” “independently verify,” or “double-check with
multiple models” to run bounded specialist calls in parallel and synthesize one
final response. Calls sharing a model are safely serialized; different resident
models or backends may run concurrently.

Ordinary chat is also routed automatically. Coding and debugging requests use
`LLM_CODING_MODEL`, analysis and planning use `LLM_REASONING_MODEL`, approved
tool/project changes use `LLM_AGENT_MODEL`, summaries and transformations use
`LLM_FAST_MODEL`, and normal conversation uses `LLM_GENERAL_MODEL`. Complex
reviews pair the reasoning model with `LLM_CRITIC_MODEL` before synthesis.
Missing specialist files fall back to the next available local model. At most
`LLM_MAX_LOADED_MODELS` remain resident per assistant instance.

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
| `LEARNING_SESSION_TTL_MINUTES` | `180` | Expiry for session-only response and reference adaptation |
| `LEARNING_LEVEL2_MIN_EVIDENCE` | `3` | Independent signals required for an inferred candidate |
| `LEARNING_SHADOW_MIN_OBSERVATIONS` | `20` | Safe shadow comparisons required before promotion |
| `LEARNING_CANARY_MIN_OBSERVATIONS` | `20` | Canary outcomes required before Level 3 promotion |
| `CURIE_LOCAL_MEMORY_DB` | `.curie_memory.sqlite3` | Durable fallback when Mongo/Postgres are unset |
| `CURIE_MEMORY_KERNEL` | `auto` | `auto` prefers the native Rust ranker, `rust` requires it, and `python` is the audited rollback |
| `CURIE_MEDIA_TRANSPORT` | `rust` | `rust` requires native bounded media transport; `auto` is compatibility mode and `python` is the audited emergency rollback |
| `CURIE_MEDIA_MAX_OUTPUT_BYTES` | `268435456` | Maximum combined or encoded media output size |
| `CURIE_CONNECTOR_GATEWAY` | `rust` | Require native connector queue admission, ordering, deadlines, cancellation, and retry policy |
| `CONNECTOR_DELIVERY_CONCURRENCY` | `4` | Maximum simultaneous outbound sends per connector within the bounded queue |
| `CURIE_DEVICE_RESOLVER` | `rust` | Require native device/entity normalization, grouping, ambiguity detection, and ranking |
| `CURIE_TASK_ENGINE` | `rust` | Require native atomic task/idempotency transactions, leases, fencing, retries, and recovery |
| `CURIE_TASK_LEASE_MS` | `30000` | Durable step lease duration; heartbeats renew long-running claims |
| `CURIE_TASK_RETRY_BASE_MS` | `100` | Base persisted backoff for safe read-only task retries |
| `MEMORY_RELEVANCE_MIN_SCORE` | `0.28` | Minimum hybrid relevance required before a memory enters the prompt |
| `MEMORY_CONTEXT_CHAR_BUDGET` | `1600` | Maximum long-term-memory characters injected into one request |
| `MEMORY_MAX_CANDIDATES` | `500` | Maximum owner-filtered records ranked during one recall |
| `MEMORY_RERANK_CANDIDATES` | `96` | Maximum plausible candidates receiving vector reranking |
| `MEMORY_RETRIEVAL_CACHE_SIZE` | `4096` | Bounded in-process cache of compiled retrieval features |
| `MEMORY_EPISODE_TTL_DAYS` | `180` | Retention window for salient conversation episodes |
| `PROACTIVE_PREDICTIONS_ENABLED` | `true` | Offer grounded, permission-seeking predicted help |
| `PROACTIVE_PREDICTION_MIN_CONFIDENCE` | `0.82` | Minimum confidence before suggesting predicted help |

Curie uses bounded hierarchical memory inspired by MemGPT/Letta: recent session
messages form working memory, stable facts and preferences form core memory,
salient user-authored goals or decisions form time-limited episodic memory, and
older project or biographical facts remain archival. Recall uses lexical,
fuzzy, recency, confidence, and reinforcement signals with a hard relevance
threshold and prompt budget. The deterministic hot path can run in Curie's
ABI3 Rust extension while storage, owner policy, learning consent, and prompt
construction remain in Python. Build it with `make memory-kernel`, then set
`CURIE_MEMORY_KERNEL=rust` for fail-closed production use. See
[Rust memory kernel](docs/RUST_MEMORY_KERNEL.md).
A cheap relevance gate runs before vector reranking, and unchanged memory
features are reused from a bounded LRU cache.
Routine operational commands bypass long-term recall so unrelated memories
cannot pull a reply back to an old topic.

Attachment inspection, file-signature validation, SHA-256 streaming, PCM/WAV
concatenation, and trained-voice/FFmpeg subprocess supervision can run through
Curie's second ABI3 Rust extension. Python continues to own speech and vision
models, transcription, connector presentation, and personality decisions.
Build it with `make media-transport`; see
[Rust media transport](docs/RUST_MEDIA_TRANSPORT.md).

Connector queue admission and device/entity resolution also have dedicated
ABI3 Rust kernels. The connector kernel handles bounded cross-event-loop
ordering without receiving message text or recipient IDs. The device kernel
handles canonical IDs, owner aliases, generic groups such as “all lights,”
room-qualified groups, ambiguity gates, close wording, and dialogue references
over a credential-free inventory projection. Python still owns provider SDKs,
authorization, persistence, actual delivery/control, and Curie's wording. See
[Rust connector gateway](docs/RUST_CONNECTOR_GATEWAY.md) and
[Rust device resolver](docs/RUST_DEVICE_RESOLVER.md).

Durable multi-step work uses a fifth ABI3 Rust engine for atomic
create-or-replay, dependency transitions, leases, fencing tokens, retries,
cancellation, deadlines, and crash recovery. Python retains planning,
permissions, approvals, tool execution, verification, compensation, and
Curie's response style. Build it with `make task-engine`; see
[Rust durable task engine](docs/RUST_TASK_ENGINE.md).

Inferred adaptations do not change live behavior directly. They become
owner-scoped candidates, pass offline and adversarial evaluation, run in shadow
mode, and require the relevant owner approval or canary gate. Every promoted
configuration is versioned and can be reversed with `/learning rollback
<config_key>`. `/learning inspect` shows candidates, provenance, evaluation,
and configuration history without storing raw private conversation text.

Run `python scripts/benchmark_memory.py` for a deterministic synthetic recall
quality and latency check. The benchmark never reads stored conversations.

All long-term items retain owner scope, provenance, confidence, expiry, and
correction history. Use `/memory stats` for content-free tier counts,
`/memory search TOPIC` for explicit recall, `/memory inspect` or `/memory why KEY`
for provenance, and `/memory forget KEY` to remove an item. “What do you remember
about TOPIC?” performs the same gated search. Ambiguous “forget that” requests a
specific target instead of deleting every memory.

Curie stores explicit memories with provenance and reinforcement counts. You can
teach a safe declarative ability with a phrase such as “When I say morning
brief, summarize my priorities.” It remains pending until you reply
`/approve skill morning_brief`; use `/reject skill morning_brief` to discard it.
Learned abilities guide responses only—they cannot install or execute generated
code or authorize consequential external actions.

Natural requests for project scaffolding, file inspection, code changes, tests,
hardware/RAM inspection, weather, and live research pass through one guarded
action router. `CURIE_WORKSPACE_ROOT` defines the master user's workspace and
`CURIE_PROJECTS_ROOT` defines isolated per-user project sandboxes. Generated
project changes receive a user-bound, 30-minute `/approve action TOKEN` prompt
before files are modified. Commands use an allowlist without a shell, filter
secrets from their environment, enforce timeouts, and write outcomes to the
local audit database. Live research retains the URLs actually fetched.
Sandboxed command execution requires Bubblewrap (`bwrap`) and permission to
create unprivileged user namespaces. If the host disables that kernel feature,
Curie fails closed and reports the command as unavailable instead of running it
without filesystem isolation.

Audit events are structured and redacted before persistence. Use `/audit` for a
retained-event and security-alert summary, `/audit export` for an owner-scoped
JSON export, or `/audit delete` followed by `/audit delete confirm` for explicit
owner-scoped deletion. `CURIE_AUDIT_RETENTION_DAYS` defaults to 90. Set
`CURIE_LOG_FILE` to enable private rotating application logs; size and backup
count are controlled by `CURIE_LOG_MAX_BYTES` and `CURIE_LOG_BACKUP_COUNT`.
Operational recovery procedures are in `docs/INCIDENT_RESPONSE.md`.

Proactive suggestions are inferred by the local reasoning model from repeated or
explicit evidence. They always ask permission before work begins and are bounded
by quiet hours, a daily cap, persisted contact cadence, and safety filters.

### Personal operations

Account setup for Gmail, X, GitHub, and the optional headless browser is
documented in [docs/ACCOUNT_INTEGRATIONS.md](docs/ACCOUNT_INTEGRATIONS.md).

Use `/birthday add NAME MM-DD[-YYYY]` to store an explicitly supplied private
birthday and `/agenda` to combine birthdays, reminders, cached OAuth calendar
events, and sourced holidays. New users receive at most one casual proactive
message daily by default. Google Calendar and Gmail connections request
read-only scopes unless a separate write workflow is explicitly started, and
refresh tokens require the operating-system credential store.

Projects must be explicitly enrolled before bounded background health checks.
Email sends, calendar writes, account submissions, code pushes, and pull-request
creation require a complete preview followed by fresh single-use approval.
Curie does not merge pull requests, deploy, alter branch protection, solve
CAPTCHAs, accept terms, invent identity details, or reuse credentials.

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
| `CURIE_WORKSPACE_ROOT` | project directory | Filesystem boundary for master actions |
| `CURIE_PROJECTS_ROOT` | `./projects` | Root for isolated user project sandboxes |
| `SYSTEMD_SERVICE_NAME` | *(none)* | Systemd service name for self-update restarts |

---

## 🏗️ Architecture Overview

The runtime uses typed tools and explicit service boundaries. See the
[Curie Enhancement Roadmap](docs/ENHANCEMENT_ROADMAP.md) for future accuracy,
relationship-quality, security, and performance work.

```
┌─────────────────────────────────────────────────────────────┐
│                     MESSAGING PLATFORMS                      │
│ Telegram │ Discord │ WhatsApp │ Slack │ REST/WS API        │
└──────────┴─────────┴──────────┴───────┴────────────────────┘
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

```
curie-ai/
├── agent/                  # Core agent logic
│   ├── chat_workflow.py    # Unified message processing pipeline
│   ├── chat_workflow.py    # Authoritative conversation workflow
│   ├── orchestration/      # Session, routing, model, learning services
│   ├── tooling/            # Typed executable capability registry
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
| [Enhancement Roadmap](docs/ENHANCEMENT_ROADMAP.md) | Canonical phased roadmap for capability, friendship, accuracy, and safety |
| [Cache Policy](docs/CACHE_POLICY.md) | Required scope, lifetime, and sensitivity rules for caches |
| [Environment Sync](docs/ENV_SYNC.md) | Safely reconcile `.env` with `.env.example` |
| [Incident Response](docs/INCIDENT_RESPONSE.md) | Response procedures for credentials, data, models, and tasks |
| [Contributing](docs/CONTRIBUTING.md) | Development and contribution workflow |
| [Code of Conduct](docs/CODE_OF_CONDUCT.md) | Community participation standards |

---

## 🛠️ Development

Captured model responses can be checked against the conversation quality suite:

```bash
python -m evaluation.runner path/to/responses.json
```

The response file is a JSON object keyed by scenario ID. Quality and latency
budgets live in `evaluation/scenarios.json`.

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

Contributions are welcome! Please read [CONTRIBUTING.md](docs/CONTRIBUTING.md) before submitting a pull request.

## 📄 License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

---

**C.U.R.I.E. — Your Personal AI Assistant, Running Locally.**

## Locally trained Curie voice

Curie owns the trained Nano speech worker, live sentence streaming, local transcription, personality delivery and training/evaluation tools in `services/trained_voice/`. The trained voice is the default for Telegram, Discord, Slack, WhatsApp, the main API, and the Observatory dashboard. Private weights and recordings live in `data/trained-voice/` and are excluded from Git. See [setup, provenance and resource limits](docs/TRAINED_VOICE.md).
