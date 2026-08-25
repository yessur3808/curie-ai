#!/usr/bin/env python3
import sys
import os

print(f"Python: {sys.executable}")
print(f"CWD: {os.getcwd()}")

try:
    from dotenv import load_dotenv

    print("✅ dotenv imported")
    load_dotenv()
    print(f"✅ .env loaded")
    print(
        "TELEGRAM_BOT_TOKEN: configured"
        if os.getenv("TELEGRAM_BOT_TOKEN")
        else "TELEGRAM_BOT_TOKEN: not configured"
    )
    print(f"RUN_TELEGRAM: {os.getenv('RUN_TELEGRAM')}")
    print(f"RUN_API: {os.getenv('RUN_API')}")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback

    traceback.print_exc()
