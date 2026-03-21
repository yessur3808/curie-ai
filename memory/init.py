# memory/init.py

from .database import init_databases
from .users import UserManager
from .conversations import ConversationManager
from .research import ResearchManager


def init_memory():
    init_databases()


__all__ = ["UserManager", "ConversationManager", "ResearchManager", "init_memory"]
