#!/bin/bash
# Clean restart script for PM2 - ensures only one instance runs

set -e

PROJECT_DIR="/home/curlycoffee3808/Desktop/server/assistant/curie00"
cd "$PROJECT_DIR"

echo "� Stopping all PM2 processes..."
pm2 kill 2>/dev/null || echo "  No PM2 daemon running"
sleep 2

echo "🔍 Killing ALL Python processes running main.py..."
pkill -9 -f "main.py" 2>/dev/null || echo "  No orphaned processes found"
sleep 2

echo "🔍 Double-checking for any remaining processes..."
REMAINING=$(ps aux | grep "[m]ain.py" | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "  ⚠️  Found $REMAINING remaining processes, force killing..."
    ps aux | grep "[m]ain.py" | awk '{print $2}' | xargs kill -9 2>/dev/null || true
    sleep 3
else
    echo "  ✅ No processes remaining"
fi

echo "⏳ Waiting 10 seconds for Telegram API to clear..."
sleep 10

echo "✅ Starting fresh PM2 instance..."
pm2 start ecosystem.config.js

sleep 3

echo ""
echo "📊 Process status:"
pm2 list

echo ""
echo "🔍 Verifying single instance:"
INSTANCES=$(pm2 jlist | grep -c "curie-main" || echo "0")
PYTHON_PROCS=$(ps aux | grep "[m]ain.py" | wc -l)
echo "  PM2 instances: $INSTANCES (should be 1)"
echo "  Python processes: $PYTHON_PROCS (should be 1)"

if [ "$INSTANCES" -eq 1 ] && [ "$PYTHON_PROCS" -eq 1 ]; then
    echo "  ✅ Single instance confirmed!"
else
    echo "  ⚠️  WARNING: Multiple instances detected!"
fi

echo ""
echo "✅ Clean restart complete!"
echo "To view logs: pm2 logs curie-main --lines 50"
