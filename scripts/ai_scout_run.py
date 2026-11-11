"""Run the shared scout at its due slot; a diagnostic entry point sharing the existing scheduler receipts."""

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv


async def run(owner):
    import httpx
    from services.ai_scout import AIScoutWorker, state_for

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
                        "text": message,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    load_dotenv()
    asyncio.run(run(args.owner))


if __name__ == "__main__":
    main()
