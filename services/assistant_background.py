"""Independent awareness and requested-research loops; no hourly message spam."""

import asyncio
import logging
import threading

from services.current_awareness import CurrentAwarenessService
from services.research_followup import ResearchFollowupWorker, set_delivery_channels

logger = logging.getLogger(__name__)


class AssistantBackgroundService:
    def __init__(self, connectors, send):
        self.connectors = connectors
        self.send = send
        self.stop_event = threading.Event()
        self.threads = []
        self.awareness = CurrentAwarenessService()
        self.research = ResearchFollowupWorker(self._send)

    async def _send(self, platform, target, text):
        connector = self.connectors.get(platform)
        return bool(connector and await self.send(connector, target, text))

    def start(self):
        set_delivery_channels(self.connectors)
        for callback, name in (
            (self._awareness_tick, "awareness"),
            (self.research.run_once, "research-followup"),
        ):
            thread = threading.Thread(
                target=self._loop, args=(callback,), name=f"curie-{name}", daemon=True
            )
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()
        set_delivery_channels(())
        for thread in self.threads:
            thread.join(timeout=2)

    def _loop(self, callback):
        while not self.stop_event.is_set():
            try:
                asyncio.run(callback())
            except Exception as exc:
                logger.warning(
                    "Assistant background cycle failed: %s", type(exc).__name__
                )
            self.stop_event.wait(30)

    async def _awareness_tick(self):
        from memory import UserManager
        from memory.repositories import get_repositories

        seen = set()
        for user in get_repositories().profiles.list_with_identities():
            owner = str(user.get("internal_id") or "")
            if not owner or owner in seen:
                continue
            seen.add(owner)
            profile = UserManager.get_user_profile(owner) or {}
            if profile.get("hourly_awareness_enabled"):
                await self.awareness.refresh(owner, profile)
