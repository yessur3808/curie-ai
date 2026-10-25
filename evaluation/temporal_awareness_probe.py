"""Manual live-model regression. Requires an isolated SQLite path and no delivery."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path


async def run():
    if os.getenv("POSTGRES_HOST") or os.getenv("MONGODB_URI"):
        raise RuntimeError("Probe requires isolated SQLite, not remote memory stores")
    from agent.chat_workflow import ChatWorkflow
    from memory import UserManager, local_store
    from memory.session_store import get_session_manager
    from services.current_awareness import CurrentAwarenessService
    from services.research_followup import (
        set_delivery_channels,
        ResearchFollowupWorker,
    )

    path = Path(os.environ.get("CURIE_LOCAL_MEMORY_DB", "")).resolve()
    if not str(path).startswith("/tmp/curie-awareness."):
        raise RuntimeError(
            "Probe requires its isolated /tmp/curie-awareness.* database"
        )
    local_store._PATH = path
    owner = UserManager.get_or_create_user_internal_id(
        channel="telegram",
        external_id="synthetic-awareness",
    )
    profile = {
        "timezone": "Asia/Hong_Kong",
        "location": "Hong Kong",
        "hourly_awareness_enabled": True,
        "autonomous_followup_enabled": True,
    }
    UserManager.update_user_profile(owner, profile)
    workflow = ChatWorkflow(max_history=8)
    workflow.learning_service.submit = lambda *args, **kwargs: None
    sessions = get_session_manager()
    sessions.reset_session("telegram", owner)
    await CurrentAwarenessService().refresh(owner, profile, force=True)
    sessions.add_message(
        "telegram",
        owner,
        "user",
        "I had a Los Angeles trip from September 30 to October 9 this year, for a friend's wedding.",
    )
    sessions.add_message(
        "telegram",
        owner,
        "assistant",
        "Would you like me to check the forecast for September 30 to October 9?",
    )
    set_delivery_channels(["telegram"])
    try:
        for index, text in enumerate(
            [
                "No, we're already on October 25. Why would you offer a forecast for past dates?",
                "Yeah, the wedding was fine. Anyway, what's today's date?",
                "What's today's weather?",
                "What's today's news?",
                "Research a detailed comparison of sodium-ion and lithium-ion batteries. Get back to me.",
                "/research status",
            ]
        ):
            result = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": owner,
                    "external_user_id": "synthetic-awareness",
                    "external_chat_id": "synthetic-awareness",
                    "message_id": f"awareness-{index}-{datetime.now().timestamp()}",
                    "text": text,
                    "timestamp": datetime.now(timezone.utc),
                }
            )
            print(
                json.dumps(
                    {
                        "user": text,
                        "assistant": result.get("text"),
                        "model": result.get("model_used"),
                        "status": result.get("execution_status"),
                        "coordination": result.get("coordination"),
                    },
                    default=str,
                ),
                flush=True,
            )
        # Run a real persisted follow-up through the Rust claim and delivery path,
        # replacing only public research and the final transport with fixtures.
        sent = []

        async def deliver(platform, target, text):
            sent.append(text)
            return True

        async def evidence(query):
            return {
                "answer": "Synthetic test evidence only [S1].",
                "sources": [{"url": "https://example.org/research-fixture"}],
            }

        worker = ResearchFollowupWorker(deliver, researcher=evidence)
        await worker.run_once()
        print(json.dumps({"followup_delivery_fixture_count": len(sent)}), flush=True)
    finally:
        set_delivery_channels(())


if __name__ == "__main__":
    asyncio.run(run())
