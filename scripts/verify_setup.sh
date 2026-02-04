#!/bin/bash
# Curie AI PM2 Startup Script

set -e

PROJECT_DIR="/home/curlycoffee3808/Desktop/server/assistant/curie00"
VENV_PYTHON="$PROJECT_DIR/ai_venv/bin/python"
VENV_PIP="$PROJECT_DIR/ai_venv/bin/pip"

echo "=========================================="
echo "Curie AI PM2 Setup & Verification"
echo "=========================================="

# Step 1: Verify Python environment
echo ""
echo "✓ Checking Python environment..."
$VENV_PYTHON --version
echo "  Python executable: $VENV_PYTHON"

# Step 2: Verify dotenv
echo ""
echo "✓ Checking dependencies..."
$VENV_PYTHON -c "import dotenv; print('  dotenv: OK')" || echo "  ❌ dotenv missing"

# Step 3: Verify .env loads
echo ""
echo "✓ Checking .env configuration..."
cd "$PROJECT_DIR"
$VENV_PYTHON -c "
import os
from dotenv import load_dotenv
load_dotenv()
print(f'  TELEGRAM_BOT_TOKEN: {\"SET\" if os.getenv(\"TELEGRAM_BOT_TOKEN\") else \"NOT SET\"}')
print(f'  RUN_TELEGRAM: {os.getenv(\"RUN_TELEGRAM\")}')
print(f'  RUN_API: {os.getenv(\"RUN_API\")}')
print(f'  POSTGRES_HOST: {os.getenv(\"POSTGRES_HOST\")}')
" || echo "  ❌ Error loading .env"

# Step 4: Verify log directory
echo ""
echo "✓ Checking log directory..."
mkdir -p "$PROJECT_DIR/log"
echo "  Log directory: OK"

# Step 5: Ready for PM2
echo ""
echo "=========================================="
echo "Ready to start with PM2!"
echo "=========================================="
echo ""
echo "Run:"
echo "  cd $PROJECT_DIR"
echo "  pm2 start ecosystem.config.js"
echo ""
echo "Monitor:"
echo "  pm2 logs curie-main"
echo "  pm2 list"
echo ""
