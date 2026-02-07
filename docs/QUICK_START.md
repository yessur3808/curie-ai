# Quick Start Guide for C.U.R.I.E. AI

Get C.U.R.I.E. up and running in 5 minutes with this streamlined guide.

## Prerequisites

- Python 3.10 or higher
- PostgreSQL and MongoDB (or use Docker)
- 10-15 minutes

## Installation Steps

### 1. Clone and Setup

```bash
# Clone the repository
git clone https://github.com/yessur3808/curie-ai.git
cd curie-ai

# Create and activate virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Optional: Install voice features and extra connectors (Discord, WhatsApp)
# Skip this if you're on Python 3.13+ or don't need these features
# pip install -r requirements-optional.txt
```

**Verify installation:**
```bash
python scripts/verify_setup.py
```

**Note:** If `pip install -r requirements-optional.txt` fails on Python 3.13+ with openai-whisper errors, that's expected. Whisper is only used for optional voice features and extra connectors, and the application will work fine without these optional dependencies.

If you see other errors, check the [Troubleshooting Guide](TROUBLESHOOTING.md).

### 2. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your preferred editor
nano .env  # or vim, code, etc.
```

**Minimal configuration for Telegram:**
```env
# Telegram Bot Token (required for Telegram)
TELEGRAM_BOT_TOKEN=your_token_from_botfather

# Database Configuration
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=curie
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
MONGO_URI=mongodb://localhost:27017/curie

# Optional: LLM Model (can skip to test without AI)
MODEL_PATH=models/your-model.gguf
```

### 3. Setup Databases

**Option A: Using Docker with Make (Easiest)**
```bash
# Start databases and run all migrations
make db-start && make setup-db
```

**Option B: Using Docker manually**
```bash
# Start PostgreSQL and MongoDB with Docker
docker-compose up -d postgres mongo

# Wait a few seconds for databases to start
sleep 5

# Run migrations
python scripts/apply_migrations.py
python scripts/gen_master_id.py
python scripts/insert_master.py
```

**Option C: Local Installation**
```bash
# Install PostgreSQL and MongoDB on your system
# Then create the database:
psql -U postgres -c "CREATE DATABASE curie;"
psql -U postgres -c "CREATE USER your_user WITH PASSWORD 'your_password';"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE curie TO your_user;"

# Run migrations
python scripts/apply_migrations.py
python scripts/gen_master_id.py
python scripts/insert_master.py
```

### 4. Get a Telegram Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/botfather)
2. Send `/newbot` and follow the instructions
3. Copy the bot token
4. Paste it in your `.env` file as `TELEGRAM_BOT_TOKEN`

### 5. Run C.U.R.I.E.

**Using Make commands (recommended):**
```bash
# Run with Telegram connector
make run-telegram

# Or run with API server
make run-api

# Or run all connectors
make run-all
```

**Or run directly:**
```bash
# Run with Telegram connector
python main.py --telegram

# Or run with API server
python main.py --api

# Or run all connectors
python main.py --all
```

You should see:
```
✅ ChatWorkflow initialized with persona: Curie
Starting Telegram connector...
```

### 6. Test It Out

Open Telegram and send a message to your bot. It should respond!

## Common Scenarios

### Running Without LLM (Testing Mode)

You can run C.U.R.I.E. without a language model for testing:
1. Don't set `MODEL_PATH` in `.env`
2. The app will warn but continue running
3. Responses will be placeholders

### Using Docker for Everything

```bash
# Start all services (databases + app)
docker-compose up

# The app will be available at:
# - Telegram: Connect to your bot
# - API: http://localhost:8000
```

### Using PM2 for Production

```bash
# Install PM2
npm install -g pm2

# Start with PM2
pm2 start ecosystem.config.js

# View logs
pm2 logs curie-main

# See status
pm2 status
```

## Next Steps

Once you have C.U.R.I.E. running:

1. **Customize your persona**: Create `assets/personality/curie.json` (for example, copy from `assets/example_persona.json`) and edit it. The `assets/personality` folder is gitignored, so your persona file won’t be committed.
2. **Add more platforms**: Configure Discord, WhatsApp, or API
3. **Enable voice**: Set up speech recognition and TTS
4. **Advanced features**: Check out the [Coding Modules Guide](CODING_MODULES_GUIDE.md)

## Common Issues

### "openai-whisper installation fails" (Python 3.13+)

**Error:**
```
Getting requirements to build wheel did not run successfully.
exit code: 1
```

**Solution:** This is expected on Python 3.13+. The core dependencies in `requirements.txt` no longer include openai-whisper. The application will work fine - voice features will automatically use SpeechRecognition as a fallback.

For more details, see [Troubleshooting Guide - openai-whisper Installation Issues](TROUBLESHOOTING.md#openai-whisper-installation-issues-python-313).

### "ModuleNotFoundError: No module named 'pytz'"

**Solution:** Install dependencies:
```bash
pip install -r requirements.txt
```

### "Could not connect to database"

**Solution:** 
1. Check databases are running: `docker-compose ps` or check system services
2. Verify connection details in `.env`
3. Test connections:
   ```bash
   psql -h localhost -U your_user -d curie
   mongosh "mongodb://localhost:27017/curie"
   ```

### Bot not responding in Telegram

**Solution:**
1. Verify `TELEGRAM_BOT_TOKEN` is correct in `.env`
2. Check the bot is running without errors: `python main.py --telegram`
3. Ensure the bot is not already running elsewhere (only one instance allowed)

## Getting Help

- **Troubleshooting**: See [Troubleshooting Guide](TROUBLESHOOTING.md)
- **Full Setup**: See main [README.md](../README.md)
- **Platform-Specific**: See [Multi-Platform Guide](MULTI_PLATFORM_GUIDE.md)
- **GitHub Issues**: [Report bugs or ask questions](https://github.com/yessur3808/curie-ai/issues)

## Useful Make Commands

For convenience, many common tasks have Make shortcuts. Run `make help` to see all available commands.

**Quick reference:**
```bash
# Installation
make install              # Install core dependencies
make install-optional     # Install optional dependencies (Whisper, Discord, WhatsApp)
make verify              # Verify your setup

# Database management
make db-start            # Start databases with Docker
make db-stop             # Stop databases
make db-status           # Check database status
make setup-db            # Run migrations (after db-start)

# Running the application
make run-telegram        # Start with Telegram
make run-discord         # Start with Discord
make run-whatsapp        # Start with WhatsApp
make run-api             # Start with API server
make run-all             # Start with all connectors

# Development
make test                # Run tests
make clean               # Clean cache files
make check-ports         # Check if ports are available
```

**Complete first-time setup:**
```bash
make install && make db-start && make setup-db && make run-telegram
```

## Verification Checklist

Before asking for help, verify:

- [ ] Python 3.10+ installed: `python --version`
- [ ] Dependencies installed: `make verify` or `python scripts/verify_setup.py`
- [ ] `.env` file configured correctly
- [ ] Databases running and accessible
- [ ] No other instance of the bot running
- [ ] Checked logs for error messages

---

**Need more help?** Check the [Troubleshooting Guide](TROUBLESHOOTING.md) or open an issue on GitHub.
