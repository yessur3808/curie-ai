"""Run the shared scout at its due slot; a diagnostic entry point sharing the existing scheduler receipts."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re

from dotenv import load_dotenv


async def run(owner):
    import httpx
    from services.ai_scout import AIScoutWorker, state_for
    from utils.formatting import telegram_html

    async def send(platform, target, message):
        token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        if platform != "telegram" or not token:
            return False
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://api.telegram.org/bot" + token + "/sendMessage",
                    json={
                        "chat_id": target,
                        "text": telegram_html(message),
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                return response.status_code == 200 and response.json().get("ok") is True
        except Exception:
            return False

    await asyncio.wait_for(AIScoutWorker(send).run_owner(owner), timeout=285)
    state = state_for(owner)
    print(
        json.dumps(
            {
                key: state.get(key)
                for key in (
                    "status",
                    "coverage",
                    "reviewer",
                    "delivery",
                    "last_completed_at",
                )
            }
        )
    )


def load_instance_environment(root=None):
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    instance = os.getenv("CURIE_INSTANCE", "curie")
    if not re.fullmatch(r"[a-z0-9_-]+", instance):
        raise ValueError("Invalid Curie instance name")
    load_dotenv(root / "instances" / f"{instance}.env", override=True)
    from services.vault_runtime import load_runtime_secrets

    load_runtime_secrets()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    load_instance_environment()
    asyncio.run(run(args.owner))


if __name__ == "__main__":
    main()
