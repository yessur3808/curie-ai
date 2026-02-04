#!/bin/bash
# Emergency stop - kills everything related to the bot

echo "🚨 EMERGENCY STOP - Killing all bot processes"

echo "Stopping PM2..."
pm2 kill 2>/dev/null || true

echo "Killing all Python main.py processes..."
pkill -9 -f "main.py" 2>/dev/null || true
pkill -9 -f "ai_venv/bin/python" 2>/dev/null || true

sleep 2

echo "Verifying all processes stopped..."
REMAINING=$(ps aux | grep -E "[m]ain.py|[a]i_venv" | wc -l)

if [ "$REMAINING" -gt 0 ]; then
    echo "⚠️ Found $REMAINING remaining processes:"
    ps aux | grep -E "[m]ain.py|[a]i_venv"
    echo ""
    echo "Force killing them..."
    ps aux | grep -E "[m]ain.py|[a]i_venv" | awk '{print $2}' | xargs kill -9 2>/dev/null || true
    sleep 2
fi

echo "✅ All processes stopped"
echo ""
echo "To start fresh: bash restart_clean.sh"
