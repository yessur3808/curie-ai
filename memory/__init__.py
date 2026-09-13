# memory/__init__.py

from .database import init_databases
from .users import UserManager
from .conversations import ConversationManager
from .research import ResearchManager
from .adaptive import (
    get_matching_abilities,
    get_relevant_memories,
    record_conversation_episode,
)
from .hierarchy import memory_stats, memory_tier, rank_memories
from .repositories import get_repositories


def init_memory():
    init_databases()


# Export everything explicitly
__all__ = [
    "init_memory",
    "UserManager",
    "ConversationManager",
    "ResearchManager",
    "get_matching_abilities",
    "get_relevant_memories",
    "record_conversation_episode",
    "rank_memories",
    "memory_stats",
    "memory_tier",
    "get_repositories",
]
