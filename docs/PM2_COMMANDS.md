# PM2 Management Commands

## Clean Restart (Recommended)
Stops all instances and starts fresh:
```bash
bash restart_clean.sh
```

## Manual Commands

### Stop Everything
```bash
pm2 kill
```

### Kill Orphaned Processes
```bash
ps aux | grep "main.py" | grep -v grep
# If any found, kill them:
pkill -9 -f "main.py"
```

### Start Fresh
```bash
pm2 start ecosystem.config.js
```

### View Status
```bash
pm2 list
pm2 info curie-main
```

### View Logs
```bash
pm2 logs curie-main
pm2 logs curie-main --lines 100
```

### Restart (Use with caution)
```bash
pm2 restart curie-main
```

## Troubleshooting Telegram Conflicts

If you see "Conflict: terminated by other getUpdates request":

1. **Stop ALL instances:**
   ```bash
   pm2 kill
   pkill -9 -f "main.py"
   ```

2. **Wait 5 seconds** for Telegram API to clear the connection

3. **Start fresh:**
   ```bash
   pm2 start ecosystem.config.js
   ```

4. **Verify only ONE instance is running:**
   ```bash
   pm2 list  # Should show instances: 1
   ps aux | grep "main.py" | grep -v grep  # Should show only ONE process
   ```

## Configuration Changes Made

- `instances: 1` - Enforces single instance
- `exec_mode: "fork"` - Uses fork mode (not cluster)
- `autorestart: true` - Restarts on crash
- `watch: false` - Doesn't restart on file changes
- `kill_timeout: 5000` - 5s graceful shutdown
- `listen_timeout: 10000` - 10s startup timeout
