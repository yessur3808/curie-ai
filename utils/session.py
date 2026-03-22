# utils/session.py

import time
import random

# In-memory mapping from user_id to timestamp (when 'busy' expires)
user_busy_until = {}


def set_busy_temporarily(user_id, min_hours=2, max_hours=5):
    """Set busy for a random duration between min_hours and max_hours."""
    duration = random.uniform(min_hours * 3600, max_hours * 3600)
    user_busy_until[user_id] = time.time() + duration


def is_user_busy(user_id):
    expiry = user_busy_until.get(user_id)
    if not expiry:
        return False
    if time.time() > expiry:
        del user_busy_until[user_id]
        return False
    return True


def clear_user_busy(user_id):
    user_busy_until.pop(user_id, None)


def small_talk_chance(user_id):
    """Reduce small talk if busy (5%), else normal (20%)."""
    return 0.05 if is_user_busy(user_id) else 0.2
