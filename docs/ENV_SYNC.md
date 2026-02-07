# .env File Sync Utility

This utility helps you manage your `.env` file by keeping it in sync with `.env.example`.

## Features

✅ **Find Missing Variables** - Identifies variables in `.env.example` that are not in your `.env`  
✅ **Safe Addition** - Adds missing variables WITHOUT overwriting existing values  
✅ **Detect Obsolete Variables** - Finds variables in `.env` that are no longer in `.env.example`  
✅ **Interactive Cleanup** - Safely remove obsolete variables with confirmation  
✅ **Automatic Backups** - Create backups before making changes  

## Usage

### Check Differences

See what's missing or obsolete without making any changes:

```bash
python scripts/sync_env.py
```

or explicitly:

```bash
python scripts/sync_env.py --check
```

### Add Missing Variables

Add all missing variables from `.env.example` to your `.env`:

```bash
python scripts/sync_env.py --sync
```

**Important:** This will NOT overwrite any existing values in your `.env`. It only adds missing variables at the end of the file.

### Remove Obsolete Variables

Interactively remove variables that are no longer in `.env.example`:

```bash
python scripts/sync_env.py --clean
```

You'll be asked to confirm each variable before removal.

### Create Backup

Always create a backup before making changes:

```bash
python scripts/sync_env.py --sync --backup
```

or

```bash
python scripts/sync_env.py --clean --backup
```

Backups are saved as `.env.backup.YYYYMMDD_HHMMSS`.

### Create .env from Scratch

If you don't have a `.env` file yet:

```bash
python scripts/sync_env.py --sync
```

This will create a new `.env` file with all variables from `.env.example`.

## Example Output

### Status Check

```
======================================================================
📊 .env FILE STATUS
======================================================================

📁 Files:
   .env.example: /home/runner/work/curie-ai/curie-ai/.env.example
   .env:         /home/runner/work/curie-ai/curie-ai/.env

⚠️  Missing Variables (3):
   These are in .env.example but not in your .env:
   • ENABLE_PROACTIVE_MESSAGING  # Proactive Messaging Configuration
   • PROACTIVE_CHECK_INTERVAL
   • ASSISTANT_NAME  # Persona/Assistant Configuration

🗑️  Potentially Obsolete Variables (2):
   These are in your .env but not in .env.example:
   • OLD_FEATURE_FLAG
   • DEPRECATED_CONFIG

💡 Suggested Actions:
   • Run with --sync to add missing variables
   • Run with --clean to interactively remove obsolete variables
======================================================================
```

### Sync Operation

```bash
$ python scripts/sync_env.py --sync --backup

✅ Backup created: /path/to/.env.backup.20260207_123456
✅ Added 3 missing variable(s) to .env
```

### Clean Operation

```bash
$ python scripts/sync_env.py --clean

🗑️  Found 2 potentially obsolete variable(s):
   (These are in your .env but not in .env.example)

   OLD_FEATURE_FLAG=true
   Remove OLD_FEATURE_FLAG? [y/N]: y
   
   DEPRECATED_CONFIG=some_value
   Remove DEPRECATED_CONFIG? [y/N]: n

✅ Removed 1 obsolete variable(s) from .env
```

## How It Works

1. **Parsing** - The script parses both `.env.example` and `.env` files, preserving comments
2. **Comparison** - It identifies differences between the two files
3. **Safe Updates** - When syncing, it ONLY adds missing variables, never overwrites existing ones
4. **Cleanup** - When cleaning, it asks for confirmation before removing each variable

## Safety Features

- ✅ Never overwrites existing values when syncing
- ✅ Always asks for confirmation when removing variables
- ✅ Creates backups with timestamps when requested
- ✅ Preserves comments from `.env.example`
- ✅ Clearly marks added variables with timestamps

## Tips

1. **Regular Sync**: Run `python scripts/sync_env.py` regularly to stay in sync with `.env.example` updates
2. **Before Updates**: Run `python scripts/sync_env.py --backup` before major updates
3. **Clean Periodically**: Use `--clean` to remove variables that are no longer needed
4. **Version Control**: Add `.env.backup.*` to your `.gitignore` (already done)

## Automation

Add to your development workflow:

```bash
# After git pull
git pull
python scripts/sync_env.py --sync

# Before committing
python scripts/sync_env.py  # Check for differences
```

## Command Reference

| Command | Description |
|---------|-------------|
| `python scripts/sync_env.py` | Check status and show differences |
| `python scripts/sync_env.py --check` | Same as above (explicit) |
| `python scripts/sync_env.py --sync` | Add missing variables (safe, won't overwrite) |
| `python scripts/sync_env.py --clean` | Interactively remove obsolete variables |
| `python scripts/sync_env.py --backup` | Create backup (combine with --sync or --clean) |
| `python scripts/sync_env.py --help` | Show help message |

## Need Help?

Run the script without arguments to see the current status and get suggestions on what to do next:

```bash
python scripts/sync_env.py
```
