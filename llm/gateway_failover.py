"""Bounded local generation recovery. Never executes tools or edits settings."""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_unhealthy_until = {}


def reset():
    with _lock:
        _unhealthy_until.clear()


def _failed(answer):
    return (
        not isinstance(answer, str) or not answer.strip() or answer.startswith("[Error")
    )


def request_with_failover(prompt, **kwargs):
    from llm.local_gateway import request

    fallback = os.getenv("LLM_FAST_MODEL", "Qwen3-4B-Q4_K_M.gguf")
    role = kwargs.get("role") or "general"
    primary = kwargs.get("model_name") or os.getenv(f"LLM_{role.upper()}_MODEL", role)
    key = (os.getenv("CURIE_INFERENCE_SOCKET"), primary)
    # No recursion and no reinterpretation of an explicitly requested alternate.
    eligible = role != "alternate" and primary != fallback and role != "fast"
    with _lock:
        cooling = eligible and _unhealthy_until.get(key, 0) > time.monotonic()
    if not cooling:
        try:
            answer = request(prompt, **kwargs)
        except PermissionError:
            raise  # Never bypass socket ownership / privacy checks.
        except (OSError, ValueError, KeyError):
            answer = "[Error: shared local inference gateway unavailable]"
        if not _failed(answer) or not eligible:
            return answer
        with _lock:
            _unhealthy_until[key] = time.monotonic() + 300
        logger.warning(
            "Local model failure; using recovery model=%s cooldown_seconds=300",
            fallback,
        )

    recovery = dict(kwargs, role="fast", model_name=fallback)
    # PM2's worker restart_delay is five seconds. Retry only recovery transport,
    # never the crashing primary, and never start a second inference process.
    for attempt in range(5):
        if attempt:
            time.sleep(2)
        try:
            answer = request(prompt, **recovery)
        except PermissionError:
            raise
        except (OSError, ValueError, KeyError):
            continue
        if not _failed(answer):
            logger.info("Local model recovery answered model=%s", fallback)
            return answer
        # A model-level error is not a transport restart; don't hammer it.
        break
    logger.warning("Local model recovery unavailable model=%s", fallback)
    return "[Error: primary and recovery local models unavailable]"
