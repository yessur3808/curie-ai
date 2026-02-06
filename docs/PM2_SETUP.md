# PM2 Setup for Curie AI

## Changes Made

### 1. Fixed Module Import Error
- ✅ Installed all dependencies from `requirements.txt` into the venv
- ✅ `python-dotenv` is installed and ready

### 2. Updated ecosystem.config.js
- Modified pm2 config to dynamically load `.env` file
- Now passes all environment variables to pm2 subprocess
- Removed hardcoded `--telegram` flag - now respects `.env` settings
- Added logging paths to `log/` directory

### 3. Wired LLM_THREADS from .env
- Updated `llm/manager.py` to read `LLM_THREADS` from .env instead of hardcoding 18
- Applies to both model preloading and lazy-loading during inference

### 4. Environment Configuration (.env)
Current settings:
```
RUN_TELEGRAM=true
RUN_API=true
RUN_CODER=false
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
LLM_MODELS=qwen2.5-3b-instruct-Q4_K_M.gguf,Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf,Meta-Llama-3-70B-Instruct-Q4_K_M.gguf
LLM_THREADS=18
POSTGRES_HOST=192.168.50.183
POSTGRES_PORT=5432
POSTGRES_DB=assistant_db
MONGODB_URI=mongodb://192.168.50.183:27017/
```

> **⚠️ Security Note:** A real Telegram bot token was previously committed here and must be treated as compromised. The token has been replaced with a placeholder. **You must regenerate/rotate your Telegram bot token** by contacting @BotFather on Telegram before using this configuration.

## Running with PM2

### Quick Start
```bash
cd /home/curlycoffee3808/Desktop/server/assistant/curie00
pm2 start ecosystem.config.js
```

### Monitor
```bash
pm2 list
pm2 logs curie-main
pm2 logs curie-main --lines 100
```

### Control
```bash
pm2 stop curie-main
pm2 restart curie-main
pm2 delete curie-main
```

### Auto-start on system boot
```bash
pm2 startup
pm2 save
```

## Required Environment

All required env vars are set in `.env`:
- ✅ `TELEGRAM_BOT_TOKEN` - Telegram bot authentication
- ✅ `RUN_TELEGRAM=true` - Enables Telegram connector
- ✅ `RUN_API=true` - Enables FastAPI connector
- ✅ Database credentials (PostgreSQL + MongoDB)
- ✅ LLM configuration (models, threads, parameters)

## Expected Startup Output

When running successfully, you should see:
1. "Logging configured with level: INFO"
2. "Initializing model and memory..."
3. "🤖 Telegram bot is running in multi-persona mode. Default: curie"
4. "Starting API (FastAPI) connector on http://0.0.0.0:8000..."
5. "🤖 Telegram bot is running..."

## Debugging

If the app doesn't start:
1. Check log files: `cat log/pm2-out.log` and `cat log/pm2-error.log`
2. Test directly: `/path/to/venv/bin/python main.py --no-init --telegram`
3. Verify dependencies: `/path/to/venv/bin/pip list | grep dotenv`
4. Check .env exists and is readable in the working directory

## Notes

- The venv is located at `./ai_venv/` in the project root
- pm2 uses absolute paths to the venv interpreter
- Working directory is set to project root so `.env` is discovered
- Logs are written to `log/pm2-out.log` and `log/pm2-error.log`
