# utils/busy.py

import re
from llm import manager

BUSY_KEYWORDS = {
    "busy", "stop", "occupied", "not now", "can't talk", "later", "do not disturb",
    "need to focus", "working", "in a meeting", "no time", "leave me alone", "be quiet"
}
RESUME_KEYWORDS = {
    "resume", "continue", "i'm back", "i am back", "free now", "can talk", "available",
    "done", "let's chat", "talk to me", "i'm here", "i am here", "let's continue"
}

def detect_busy_intent(text):
    """Returns True if text indicates user wants to pause/stop."""
    text = text.lower()
    for phrase in BUSY_KEYWORDS:
        if phrase in text:
            return True
    if re.search(r'\bbusy\b|\bstop\b|\boccupied\b', text):
        return True
    return False

def detect_resume_intent(text):
    """Returns True if text indicates user wants to resume."""
    text = text.lower()
    for phrase in RESUME_KEYWORDS:
        if phrase in text:
            return True
    if re.search(r'\bresume\b|\bcontinue\b|\bback\b|\bfree\b', text):
        return True
    return False
