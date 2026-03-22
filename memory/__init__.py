# memory/__init__.py

from .database import init_databases
from .users import UserManager
from .conversations import ConversationManager
from .research import ResearchManager


def init_memory():
    init_databases()


# Export everything explicitly
__all__ = ["init_memory", "UserManager", "ConversationManager", "ResearchManager"]
