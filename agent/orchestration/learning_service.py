"""Bounded, best-effort learning after completed conversations."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ConversationLearningService:
    def __init__(self, executor, learner=None):
        self.executor = executor
        self.learner = learner

    def submit(
        self,
        internal_id: str,
        user_text: str,
        response: str,
        *,
        source_message_id: str = "",
        source_channel: str = "unknown",
    ) -> None:
        try:
            learner = self.learner
            if learner is None:
                from memory.learning import learn_from_exchange

                learner = learn_from_exchange
            if source_message_id or source_channel != "unknown":
                self.executor.submit(
                    learner,
                    internal_id,
                    user_text,
                    response,
                    source_message_id=source_message_id,
                    source_channel=source_channel,
                )
            else:
                self.executor.submit(learner, internal_id, user_text, response)
        except Exception as exc:
            logger.debug("Conversation learning unavailable: %s", exc)
