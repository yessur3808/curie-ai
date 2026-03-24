# Quick Start Guide — C.U.R.I.E. AI

Get C.U.R.I.E. up and running in about 10 minutes.

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.10 or higher |
| PostgreSQL | Any recent version (or use Docker) |
| MongoDB | Any recent version (or use Docker) |
| GGUF model | e.g. `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` |

---

## Step 1 — Clone & install

```bash
git clone https://github.com/yessur3808/curie-ai.git
cd curie-ai

# Create and activate a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# Install core dependencies
pip install -r requirements.txt   # or: make install

# Optional: voice (Whisper STT/TTS), Discord, and WhatsApp support
# pip install -r requirements-optional.txt   # or: make install-optional

# Verify that everything installed correctly
python scripts/verify_setup.py    # or: make verify
```

> **Python 3.13+ note:** `openai-whisper` may fail to build. Skip `requirements-optional.txt` if you don't need voice features — the application works fine without them.

---

## Step 2 — Configure your environment

```bash
cp .env.example .env
# Edit .env with your preferred editor (nano, vim, VS Code…)
```

**Minimum required settings:**

```env
# Telegram bot token (from @BotFather on Telegram)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token

# GGUF model filename — place the file in a models/ directory
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

# Internal master user ID (UUID used for authorization checks). Leave blank to let setup scripts handle it.
MASTER_USER_ID=

# Your personal Telegram user ID (numeric, from a Telegram helper bot such as @userinfobot).
MASTER_TELEGRAM_ID=123456789

# Secret username for the master user (used by make setup-db / scripts/insert_master.py).
MASTER_SECRET_USERNAME=your_secret_master_username
```

> **Tip:** Run `make verify` after editing `.env` to catch configuration issues early.

---

## Step 3 — Start databases

### Option A: Docker (easiest)

```bash
# Start PostgreSQL and MongoDB containers
docker-compose up -d postgres mongo

# Run migrations and create the master user record
make setup-db
```

### Option B: Manual setup

```bash
# Run migrations
python scripts/apply_migrations.py

# Generate and insert the master user (updates MASTER_USER_ID in .env)
python scripts/gen_master_id.py --env
python scripts/insert_master.py
```

---

## Step 4 — Download a GGUF model

Place a compatible `.gguf` file in a `models/` directory in the project root:

```bash
mkdir -p models
# Download from HuggingFace, for example:
# https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF
```

Then set the filename in `.env`:

```env
LLM_MODELS=Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
```

**Recommended models by hardware:**

| RAM / VRAM | Recommended Model |
|---|---|
| 8 GB | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf |
| 16 GB | Meta-Llama-3-13B-Instruct-Q4_K_M.gguf |
| 32 GB+ | Meta-Llama-3-70B-Instruct-Q4_K_M.gguf |

---

## Step 5 — Get a Telegram bot token

1. Open Telegram and start a chat with [@BotFather](https://t.me/botfather)
2. Send `/newbot` and follow the prompts
3. Copy the token BotFather gives you
4. Paste it into your `.env` as `TELEGRAM_BOT_TOKEN`

---

## Step 6 — Run C.U.R.I.E.

```bash
# Using Make shortcuts (recommended):
make run-telegram    # Telegram only
make run-api         # REST/WebSocket API on port 8000 only
make run-all         # All enabled connectors

# Or directly:
python main.py --telegram
python main.py --api
python main.py --all
```

You should see output similar to:

```
✅ ChatWorkflow initialized with persona: Sentinel
Starting Telegram connector...
```

Open Telegram, find your bot, and send it a message — it should respond!

---

## Step 7 — (Optional) Customize your persona

C.U.R.I.E. ships with built-in personas in `assets/personality/`:

| Filename | Personality |
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

You can also create a custom persona by copying any of the files above and editing the JSON.

---

## Running in Production

### PM2 (recommended for servers)

```bash
npm install -g pm2
pm2 start ecosystem.config.js
pm2 logs curie-main
pm2 status
```

See [PM2_SETUP.md](PM2_SETUP.md) for full configuration details.

### Docker Compose (databases only)

The included `docker-compose.yml` provisions PostgreSQL and MongoDB. The application itself runs on the host.

---

## Useful Make Commands

```bash
# Installation
make install              # Install core dependencies
make install-optional     # Install optional deps (voice, Discord, WhatsApp)
make verify               # Verify setup and imports

# Database
make db-start             # Start databases with Docker
make db-stop              # Stop database containers
make db-status            # Check container status
make setup-db             # Run migrations (after db-start)

# Running
make run-telegram         # Start Telegram connector
make run-discord          # Start Discord connector
make run-api              # Start REST/WebSocket API
make run-all              # Start all connectors

# Development
make test                 # Run tests
make lint                 # Lint with flake8
make format               # Format with black
make clean                # Remove cache files
make check-ports          # Check if required ports are available
```

**Complete first-time setup in one line:**

```bash
make install && make db-start && make setup-db && make run-telegram
```

---

## Common Issues

### "openai-whisper installation fails" (Python 3.13+)

**Expected behavior.** Skip `requirements-optional.txt`. Voice features will use SpeechRecognition as a fallback and the application will work normally.

### "ModuleNotFoundError: No module named …"

Install core dependencies:

```bash
pip install -r requirements.txt
```

Then run `make verify` to check what's missing.

### "Could not connect to database"

1. Check containers are running: `docker-compose ps`
2. Verify credentials in `.env` match what you set up
3. Test connections manually:

```bash
psql -h localhost -U your_pg_user -d assistant_db
mongosh "mongodb://localhost:27017/assistant_db"
```

### "Bot not responding in Telegram"

1. Verify `TELEGRAM_BOT_TOKEN` is correct
2. Only one instance can run per token — stop any other running instances
3. Check logs for errors: `python main.py --telegram` (or `pm2 logs curie-main`)

---

## Next Steps

- **Read the main README** for a full feature reference and API documentation
- **Add more platforms**: Configure `RUN_DISCORD=true` or `RUN_WHATSAPP=true` in `.env`
- **Enable voice**: Install `requirements-optional.txt` and set `WHISPER_MODEL=base`
- **Connect cloud LLMs**: Add `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` and update `LLM_PROVIDER_PRIORITY`
- **Explore coding features**: See [ADVANCED_CODING_FEATURES.md](ADVANCED_CODING_FEATURES.md)
- **Troubleshoot**: See [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

**Need help?** Open an issue at [github.com/yessur3808/curie-ai](https://github.com/yessur3808/curie-ai/issues).
