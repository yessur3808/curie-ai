# Troubleshooting Guide for C.U.R.I.E. AI

This guide helps resolve common issues when setting up and running C.U.R.I.E.

## Table of Contents
- [Setup Issues](#setup-issues)
- [Dependency Issues](#dependency-issues)
- [Runtime Errors](#runtime-errors)
- [Database Issues](#database-issues)
- [Platform-Specific Issues](#platform-specific-issues)

---

## Setup Issues

### Setup Verification

Before troubleshooting, run the setup verification script to identify missing dependencies:

```bash
python scripts/verify_setup.py
```

This will check:
- Python version (requires 3.10+)
- Required dependencies
- Optional dependencies
- Configuration files
- Environment variables

---

## Dependency Issues

### ModuleNotFoundError: No module named 'pytz' (or any other module)

**Symptoms:**
```
ModuleNotFoundError: No module named 'pytz'
```

**Cause:** Dependencies from `requirements.txt` are not installed in your Python environment.

**Solution:**

1. **Install all dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Verify installation:**
   ```bash
   python scripts/verify_setup.py
   ```

3. **Check your Python environment:**
   - If using a virtual environment, make sure it's activated
   - Check which Python is being used: `which python` or `which python3`
   - Verify pip installs to the correct location: `pip --version`

**Using Virtual Environment (Recommended):**

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Verify
python scripts/verify_setup.py
```

### llama-cpp-python Installation Issues

**Issue:** `llama-cpp-python` may fail to install or require special configuration.

**Solution:**

1. **Install with CPU-only support (default):**
   ```bash
   pip install llama-cpp-python
   ```

2. **Install with GPU support (CUDA):**
   ```bash
   CMAKE_ARGS="-DLLAMA_CUBLAS=on" pip install llama-cpp-python
   ```

3. **Install with Metal support (Mac M1/M2):**
   ```bash
   CMAKE_ARGS="-DLLAMA_METAL=on" pip install llama-cpp-python
   ```

See [llama-cpp-python documentation](https://github.com/abetlen/llama-cpp-python) for more options.

### discord.py or whatsapp-web.py Not Available

**Symptoms:**
```
Discord connector not available (discord.py not installed)
WhatsApp connector not available (whatsapp-web.py not installed)
```

**Cause:** These are optional dependencies that may not install correctly on all systems.

**Solution:**

These warnings are **normal** if you don't plan to use Discord or WhatsApp. The application will work fine with other connectors (Telegram, API).

To install them:
```bash
pip install discord.py==2.4.0
pip install whatsapp-web.py==0.0.8
```

---

## Runtime Errors

### Application Won't Start

**Symptoms:**
- Import errors
- Database connection errors
- Missing configuration

**Solution:**

1. **Run verification script:**
   ```bash
   python scripts/verify_setup.py
   ```

2. **Check .env configuration:**
   ```bash
   # Copy example file if .env doesn't exist
   cp .env.example .env
   
   # Edit .env and set your values
   nano .env  # or use your preferred editor
   ```

3. **Verify required environment variables:**
   - `TELEGRAM_BOT_TOKEN` (if using Telegram)
   - `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
   - `MONGO_URI`

4. **Check database connectivity:**
   ```bash
   # Test PostgreSQL connection
   psql -h localhost -U your_user -d your_database
   
   # Test MongoDB connection
   mongosh "your_mongo_uri"
   ```

### LLM Model Not Found

**Symptoms:**
```
⚠️  LLM model unavailable: FileNotFoundError
Continuing without LLM - text responses will be placeholders
```

**Cause:** GGUF model file not found or path not configured correctly.

**Solution:**

1. **Download a GGUF model:**
   - Visit [HuggingFace](https://huggingface.co/models?search=gguf)
   - Download a model (e.g., Llama 3.1 GGUF)
   - Place it in your models directory

2. **Configure model path in .env:**
   ```bash
   MODEL_PATH=/path/to/your/model.gguf
   ```

3. **Or continue without LLM:**
   The application will run in a limited mode without language model inference.

---

## Database Issues

### PostgreSQL Connection Failed

**Symptoms:**
```
psycopg2.OperationalError: could not connect to server
```

**Solution:**

1. **Check PostgreSQL is running:**
   ```bash
   # Linux
   sudo systemctl status postgresql
   
   # Mac
   brew services list
   
   # Docker
   docker ps | grep postgres
   ```

2. **Verify connection details in .env:**
   ```bash
   POSTGRES_HOST=localhost
   POSTGRES_PORT=5432
   POSTGRES_DB=curie
   POSTGRES_USER=your_user
   POSTGRES_PASSWORD=your_password
   ```

3. **Create database if needed:**
   ```bash
   psql -U postgres
   CREATE DATABASE curie;
   CREATE USER your_user WITH PASSWORD 'your_password';
   GRANT ALL PRIVILEGES ON DATABASE curie TO your_user;
   ```

4. **Run migrations:**
   ```bash
   python scripts/run_migrations.py
   ```

### MongoDB Connection Failed

**Symptoms:**
```
pymongo.errors.ServerSelectionTimeoutError
```

**Solution:**

1. **Check MongoDB is running:**
   ```bash
   # Linux
   sudo systemctl status mongod
   
   # Mac
   brew services list
   
   # Docker
   docker ps | grep mongo
   ```

2. **Verify MONGO_URI in .env:**
   ```bash
   MONGO_URI=mongodb://localhost:27017/curie
   ```

3. **Test connection:**
   ```bash
   mongosh "mongodb://localhost:27017/curie"
   ```

---

## Platform-Specific Issues

### Telegram Bot Not Responding

**Solution:**

1. **Verify bot token:**
   - Check `TELEGRAM_BOT_TOKEN` in .env
   - Test token with BotFather on Telegram

2. **Check bot is running:**
   ```bash
   python main.py --telegram
   ```

3. **Review logs for errors:**
   - Look for authentication errors
   - Check network connectivity

### Discord Bot Not Working

**Solution:**

1. **Verify discord.py is installed:**
   ```bash
   pip install discord.py==2.4.0
   ```

2. **Check bot token and intents:**
   - `DISCORD_BOT_TOKEN` in .env
   - Ensure proper intents are enabled in Discord Developer Portal

3. **Run with Discord connector:**
   ```bash
   python main.py --discord
   ```

---

## Still Having Issues?

If you're still experiencing problems:

1. **Check logs:**
   - Look for error messages and stack traces
   - Enable debug logging: `LOG_LEVEL=DEBUG` in .env

2. **Search existing issues:**
   - Check [GitHub Issues](https://github.com/yessur3808/curie-ai/issues)

3. **Create a new issue:**
   - Include output from `python scripts/verify_setup.py`
   - Include relevant error messages and logs
   - Describe what you've already tried

4. **Ask for help:**
   - Open an issue on GitHub
   - Provide system information (OS, Python version, etc.)
