# agent/chat_workflow.py
"""
Centralized chat workflow: handles all chat intelligence independent of connectors.
- Persona application & management
- Conversation memory loading/saving
- User fact retrieval (explicit-only, no auto-extraction)
- Prompt construction with structured message format
- LLM inference with output sanitation
- Deduplication at the chat level
- System / CLI commands skill integration (status, metrics, tasks, doctor, logs, start/stop/restart)
- Coding assistant skill integration
- Reminders & scheduling skill integration
- Trip / vacation planning skill integration
- Proactive filtered learning (auto-extract user preferences)
- Long-conversation summarisation to stay within context limits
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
import pytz
from datetime import datetime
from typing import Optional, Dict, Tuple
from collections import OrderedDict
from threading import Lock

from memory import UserManager
from memory.session_store import get_session_manager
from llm import manager as llm_manager
from agent.personality_context import PersonalityContext
from utils.persona import normalize_persona
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

# Task tracking (optional – import silently ignored if cli package unavailable)
try:
    from cli.tasks import (
        register_task,
        register_sub_agent,
        update_sub_agent,
        finish_task as _finish_task,
    )

    _TASK_TRACKING = True
except Exception:
    _TASK_TRACKING = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operator-configured defaults for new / anonymous users
# ---------------------------------------------------------------------------
# These env vars let the bot owner configure a sensible fallback timezone and
# location so the assistant can give accurate time/date answers before a user
# has set their own preferences through conversation.
#
# Set them in .env:
#   DEFAULT_TIMEZONE=Europe/London
#   DEFAULT_LOCATION=London, UK
_DEFAULT_TIMEZONE: str = os.getenv("DEFAULT_TIMEZONE", "UTC").strip()
_DEFAULT_LOCATION: str = os.getenv("DEFAULT_LOCATION", "").strip()

# Dedicated small thread pool for background learning tasks.
# max_workers=2 caps concurrent LLM-based fact extractions without starving
# the main event-loop thread pool used for DB I/O.
_LEARNING_EXECUTOR = _ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="curie-learning"
)

# Maximum number of lines a single history message is truncated to when building prompts.
_SUMMARY_CONTENT_MAX_LENGTH = 200


def _select_relevant_facts(user_profile: dict, query: str, top_n: int = 8) -> dict:
    """Return the most query-relevant facts from *user_profile*.

    This is a lightweight keyword-overlap information retrieval step — a form
    of Retrieval-Augmented Generation (RAG) that works without embeddings or
    vector databases.  Instead of injecting the entire user profile into every
    prompt (which wastes context-window space), only the facts most likely to
    be useful for the current message are included.

    Algorithm
    ---------
    1. Tokenise the query into a set of lowercase words.
    2. Score each fact by the number of words it shares with the query.
    3. Always include a small set of critical identity facts (name, timezone,
       language, location) regardless of their overlap score.
    4. Return the union of critical facts + top-N scored facts.

    Parameters
    ----------
    user_profile:  Full dict of learned user facts.
    query:         The user's current message.
    top_n:         Maximum number of scored (non-critical) facts to include.
    """
    if not user_profile:
        return {}

    # Facts that are always injected — they provide essential context for every reply.
    _CRITICAL_KEYS = frozenset(
        {
            "name",
            "preferred_name",
            "timezone",
            "location",
            "language",
            # Preference keys that are always relevant
            "dietary_preferences",
            "travel_style",
            "occupation",
            "interests",
            "reminders_preference",
        }
    )
    critical = {k: v for k, v in user_profile.items() if k in _CRITICAL_KEYS}

    # Score remaining facts by keyword overlap with the query
    query_words = set(query.lower().split())
    scored = []
    for k, v in user_profile.items():
        if k in _CRITICAL_KEYS:
            continue
        fact_text = f"{k} {v}".lower()
        score = sum(1 for w in query_words if w in fact_text)
        scored.append((score, k, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_facts = {k: v for score, k, v in scored[:top_n] if score > 0}
    # If no facts overlap with the query, fall back to the top_n most recently scored
    if not top_facts:
        top_facts = {k: v for _, k, v in scored[:top_n]}

    return {**critical, **top_facts}


class MessageDedupeCache:
    """
    Platform-agnostic deduplication cache for incoming messages.
    Stores processed message IDs with TTL to prevent duplicate responses.
    Key format: {platform}:{external_chat_id}:{message_id}
    """

    def __init__(self, ttl_seconds=600, max_size=5000):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self.cache = OrderedDict()  # {key: (timestamp, response)}
        self.lock = Lock()

    def _cleanup_expired(self):
        """Remove expired entries."""
        now = time.time()
        expired = [
            k for k, (ts, _) in self.cache.items() if now - ts > self.ttl_seconds
        ]
        for k in expired:
            del self.cache[k]

    def get(
        self, platform: str, external_chat_id: str, message_id: str
    ) -> Optional[str]:
        """Get cached response if message was already processed. Returns None if not found or expired."""
        key = f"{platform}:{external_chat_id}:{message_id}"
        with self.lock:
            self._cleanup_expired()
            if key in self.cache:
                ts, response = self.cache[key]
                logger.debug(f"Dedupe cache hit: {key}")
                return response
        return None

    def set(self, platform: str, external_chat_id: str, message_id: str, response: str):
        """Store a processed message and its response."""
        key = f"{platform}:{external_chat_id}:{message_id}"
        with self.lock:
            self.cache[key] = (time.time(), response)
            # FIFO eviction when cache exceeds max_size
            while len(self.cache) > self.max_size:
                self.cache.popitem(last=False)
            logger.debug(f"Dedupe cache set: {key}")


class PromptCache:
    """
    LRU cache for tokenized prompts to avoid repeated tokenization.
    Keys are hashes of (internal_id + system_prompt + user_facts + recent_history).
    internal_id is included so different users never share a cache entry.
    """

    def __init__(self, max_size=100):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.lock = Lock()
        self.hits = 0
        self.misses = 0

    def _make_key(
        self,
        system_prompt: str,
        user_facts: Dict,
        history_str: str,
        time_bucket: str = "",
        internal_id: str = "",
    ) -> str:
        """
        Create a hash key from prompt components.
        internal_id is included so two users with identical profiles never
        share a cache entry.
        """
        facts_str = json.dumps(user_facts, sort_keys=True) if user_facts else ""
        combined = f"{internal_id}|||{system_prompt}|||{facts_str}|||{history_str}|||{time_bucket}"
        return str(hash(combined))

    def get(
        self,
        system_prompt: str,
        user_facts: Dict,
        history_str: str,
        time_bucket: str = "",
        internal_id: str = "",
    ) -> Optional[Tuple]:
        """Returns (prompt_text, token_count) or None."""
        key = self._make_key(
            system_prompt, user_facts, history_str, time_bucket, internal_id
        )
        with self.lock:
            if key in self.cache:
                self.hits += 1
                entry = self.cache.pop(key)
                self.cache[key] = entry  # Move to end (LRU)
                return entry
            self.misses += 1
        return None

    def set(
        self,
        system_prompt: str,
        user_facts: Dict,
        history_str: str,
        prompt_text: str,
        token_count: int,
        time_bucket: str = "",
        internal_id: str = "",
    ):
        """Store a tokenized prompt."""
        key = self._make_key(
            system_prompt, user_facts, history_str, time_bucket, internal_id
        )
        with self.lock:
            if key in self.cache:
                del self.cache[key]
            self.cache[key] = (prompt_text, token_count)
            # Evict oldest if exceeds max
            while len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def stats(self) -> Dict:
        """Return cache hit/miss statistics."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total": total,
            "hit_rate_percent": round(hit_rate, 1),
            "size": len(self.cache),
        }


class ChatWorkflow:
    """
    Centralized chat intelligence, independent of connectors.

    Normalized input format:
    {
        'platform': str (e.g., 'telegram', 'api', 'voice'),
        'external_user_id': str or int,
        'external_chat_id': str or int,
        'message_id': str or int,
        'text': str,
        'timestamp': datetime or float (unix timestamp),
        'internal_id': str (optional) - pre-identified internal user ID (bypasses lookup if provided)
    }

    Output format:
    {
        'text': str (response),
        'timestamp': datetime,
        'model_used': str,
        'processing_time_ms': float
    }
    """

    # Output sanitation patterns
    SPEAKER_TAG_PATTERN = re.compile(
        r"^\s*(?:User:|Curie:|Assistant:|Coder:|System:)",
        re.IGNORECASE | re.MULTILINE,
    )
    META_NOTE_PATTERN = re.compile(
        r"\[(?:Note|Meta|Aside|System):[^\]]*\]", re.IGNORECASE
    )
    ACTION_PATTERN = re.compile(r"\*[^*]*\*")  # *gestures*, *smiles*, etc.
    CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```|```[\s\S]*$", re.MULTILINE)
    INLINE_CODE_PATTERN = re.compile(r"`[^`]+`")

    def __init__(
        self,
        persona: Optional[Dict] = None,
        max_history: int = 5,
        enable_small_talk: bool = False,
        idle_threshold_minutes: int = 30,
        minimal_sanitization: bool = True,
    ):
        self.persona = (
            normalize_persona(persona) if persona else self._load_default_persona()
        )
        self.max_history = max_history
        self.enable_small_talk = enable_small_talk
        self.idle_threshold_minutes = idle_threshold_minutes
        self.minimal_sanitization = minimal_sanitization
        self.personality_context = PersonalityContext(self.persona)

        self.dedupe_cache = MessageDedupeCache(ttl_seconds=600, max_size=5000)
        self.prompt_cache = PromptCache(max_size=100)

        logger.info(
            f"ChatWorkflow initialized with persona: {self.persona.get('name', 'Unknown')}"
        )

    def _load_default_persona(self) -> Dict:
        """Load default persona from personality.json if not provided."""
        persona_file = os.path.join(
            os.path.dirname(__file__), "..", "assets", "personality", "personality.json"
        )
        if os.path.exists(persona_file):
            with open(persona_file) as f:
                return normalize_persona(json.load(f))
        return normalize_persona(
            {
                "name": "Assistant",
                "description": "Default assistant persona",
                "system_prompt": "You are a helpful assistant.",
            }
        )

    async def process_message(self, normalized_input: Dict) -> Dict:
        """
        Main entry point: process a normalized message and return structured response.
        """
        start_time = time.time()

        platform = normalized_input.get("platform", "unknown")
        external_user_id = normalized_input.get("external_user_id")
        external_chat_id = normalized_input.get("external_chat_id")
        message_id = str(normalized_input.get("message_id", ""))
        user_text = normalized_input.get("text", "").strip()

        # task_id is always defined so later _finish_task calls are safe even
        # when task tracking is disabled or register_task is called later.
        task_id = str(uuid.uuid4())[:8]

        if not all([external_user_id, external_chat_id, user_text]):
            logger.error(
                f"Invalid input: missing required fields. Input: {normalized_input}"
            )
            return {
                "text": "[Error: Invalid message format]",
                "timestamp": datetime.utcnow(),
                "model_used": "N/A",
                "processing_time_ms": 0,
            }

        # Resolve internal_id — use pre-identified one if provided, otherwise lookup/create
        internal_id = normalized_input.get("internal_id")
        if not internal_id:
            internal_id = UserManager.get_or_create_user_internal_id(
                channel=platform,
                external_id=str(external_user_id),
                secret_username=f"{platform}_{external_user_id}",
                updated_by="chat_workflow",
            )

        # Deduplication cache check
        cached_response = self.dedupe_cache.get(
            platform, str(external_chat_id), message_id
        )
        if cached_response:
            processing_time = (time.time() - start_time) * 1000
            return {
                "text": cached_response,
                "timestamp": datetime.utcnow(),
                "model_used": "dedupe_cache",
                "processing_time_ms": round(processing_time, 2),
            }

        # ── Per-user session commands ─────────────────────────────────────────
        # Any user can manage their own conversation history.
        # These are handled before the LLM so they never consume tokens.
        command = user_text.strip().lower()

        try:
            if command in ("/reset", "/new"):
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: get_session_manager().reset_session(platform, internal_id),
                )
                reset_response = (
                    "✅ Your conversation history has been cleared. Fresh start!"
                )
                processing_time = (time.time() - start_time) * 1000
                return {
                    "text": reset_response,
                    "timestamp": datetime.utcnow(),
                    "model_used": "system",
                    "processing_time_ms": round(processing_time, 2),
                }

            if command == "/history":
                loop = asyncio.get_running_loop()
                history = await loop.run_in_executor(
                    None,
                    lambda: get_session_manager().get_history(platform, internal_id),
                )
                count = len(history)
                stats_response = (
                    f"📊 Your session: {count} messages stored.\n"
                    f"Use /reset to clear your history."
                )
                processing_time = (time.time() - start_time) * 1000
                return {
                    "text": stats_response,
                    "timestamp": datetime.utcnow(),
                    "model_used": "system",
                    "processing_time_ms": round(processing_time, 2),
                }
        except Exception:
            logger.exception(
                "Error while handling session command '%s' for user %s",
                command,
                internal_id,
            )
            processing_time = (time.time() - start_time) * 1000
            return {
                "text": "[Error: Unable to manage conversation history right now. Please try again later.]",
                "timestamp": datetime.utcnow(),
                "model_used": "system",
                "processing_time_ms": round(processing_time, 2),
            }
        # ─────────────────────────────────────────────────────────────────────

        # ── Task tracking ─────────────────────────────────────────────────
        # Registered here — after input validation, dedupe, and session-command
        # shortcuts — so no tasks are left in a permanent "running" state for
        # those fast-exit paths.
        if _TASK_TRACKING:
            try:
                register_task(
                    task_id,
                    description=user_text[:80] if user_text else "(empty)",
                    channel=platform,
                )
            except Exception:
                pass

        try:
            # ── System / CLI commands (highest priority – no LLM tokens consumed) ──
            try:
                from agent.skills.system_commands import (
                    handle_system_command,
                )  # noqa: PLC0415

                sys_response = handle_system_command(
                    user_text, internal_id=internal_id, platform=platform
                )
                if sys_response is not None:
                    logger.info("System-commands skill handled the query")
                    sm = get_session_manager()
                    sm.add_message(platform, internal_id, "user", user_text)
                    sm.add_message(platform, internal_id, "assistant", sys_response)
                    self.dedupe_cache.set(
                        platform, str(external_chat_id), message_id, sys_response
                    )
                    processing_time = (time.time() - start_time) * 1000
                    if _TASK_TRACKING:
                        try:
                            _finish_task(task_id)
                        except Exception:
                            pass
                    return {
                        "text": sys_response,
                        "timestamp": datetime.utcnow(),
                        "model_used": "system_commands_skill",
                        "processing_time_ms": round(processing_time, 2),
                    }
            except Exception as e:
                logger.debug(f"System-commands skill check failed: {e}")

            # ── Parallel skill dispatch ────────────────────────────────────────
            # All specialist sub-agents are launched simultaneously via
            # asyncio.gather().  The first non-None result wins; all others are
            # marked "skipped".  This cuts total skill-check latency to that of
            # the single slowest skill instead of the sum of all skill checks.

            async def _try_skill(coro):
                """Run a skill coroutine safely; return None on any error."""
                try:
                    return await coro
                except Exception as exc:
                    logger.debug("Skill error: %s", exc)
                    return None

            # Collect (sa_id, role, description, coroutine) for every skill.
            _skill_specs: list = []

            try:
                from agent.skills.coding_assistant import handle_coding_query  # noqa

                _skill_specs.append(
                    (
                        "coding_skill",
                        "coding_assistant",
                        "Scanning for coding / programming query",
                        handle_coding_query(user_text),
                    )
                )
            except Exception:
                pass

            try:
                from agent.skills.navigation import handle_navigation_query  # noqa

                _skill_specs.append(
                    (
                        "navigation_skill",
                        "navigation",
                        "Scanning for navigation / traffic query",
                        handle_navigation_query(user_text),
                    )
                )
            except Exception:
                pass

            try:
                from agent.skills.scheduler import handle_reminder_query  # noqa

                _skill_specs.append(
                    (
                        "scheduler_skill",
                        "scheduler",
                        "Scanning for reminder / scheduling query",
                        handle_reminder_query(
                            user_text, internal_id=internal_id, platform=platform
                        ),
                    )
                )
            except Exception:
                pass

            try:
                from agent.skills.trip_planner import handle_trip_query  # noqa

                _skill_specs.append(
                    (
                        "trip_planner_skill",
                        "trip_planner",
                        "Scanning for trip / vacation planning query",
                        handle_trip_query(user_text, internal_id=internal_id),
                    )
                )
            except Exception:
                pass

            try:
                from agent.skills.network_analyzer import (  # noqa
                    handle_network_analyzer_query,
                )

                _skill_specs.append(
                    (
                        "network_analyzer_skill",
                        "network_analyzer",
                        "Scanning for network traffic / protocol analysis query",
                        handle_network_analyzer_query(user_text),
                    )
                )
            except Exception:
                pass

            try:
                from agent.skills.network_scanner import (  # noqa
                    handle_network_scanner_query,
                )

                _skill_specs.append(
                    (
                        "network_scanner_skill",
                        "network_scanner",
                        "Scanning for network reconnaissance / port scan query",
                        handle_network_scanner_query(user_text),
                    )
                )
            except Exception:
                pass

            try:
                from agent.skills.http_interceptor import (  # noqa
                    handle_http_interceptor_query,
                )

                _skill_specs.append(
                    (
                        "http_interceptor_skill",
                        "http_interceptor",
                        "Scanning for HTTP/S interception / web vulnerability query",
                        handle_http_interceptor_query(user_text),
                    )
                )
            except Exception:
                pass

            # Register all skill sub-agents up-front so the visualization
            # shows every agent as "running" during the parallel check.
            if _TASK_TRACKING and _skill_specs:
                for _sid, _srole, _sdesc, _ in _skill_specs:
                    try:
                        register_sub_agent(
                            task_id, _sid, role=_srole, description=_sdesc
                        )
                    except Exception:
                        pass

            # Run all skill checks concurrently; stop as soon as one returns a
            # non-None response and cancel the remaining tasks.
            _skill_response: Optional[str] = None
            _skill_model: str = "skill"
            _skill_results: dict = {}  # sid -> result
            if _skill_specs:
                _fut_to_spec: dict = {
                    asyncio.ensure_future(_try_skill(spec[3])): spec
                    for spec in _skill_specs
                }
                _pending = set(_fut_to_spec.keys())
                while _pending and _skill_response is None:
                    _done, _pending = await asyncio.wait(
                        _pending, return_when=asyncio.FIRST_COMPLETED
                    )
                    for _fut in _done:
                        _sid, _srole, _sdesc, _ = _fut_to_spec[_fut]
                        try:
                            _result = _fut.result()
                        except Exception:
                            _result = None
                        _skill_results[_sid] = _result
                        if _result and _skill_response is None:
                            _skill_response = _result
                            _skill_model = _sid
                            logger.info("%s handled the query", _srole)
                # Cancel any tasks that are still pending (no longer needed).
                for _fut in _pending:
                    _fut.cancel()
                if _pending:
                    await asyncio.gather(*_pending, return_exceptions=True)

            # Update task-tracking for every skill regardless of outcome.
            for _sid, _srole, _sdesc, _ in _skill_specs:
                if _TASK_TRACKING:
                    try:
                        _summary = (
                            "handled"
                            if _sid == _skill_model and _skill_response
                            else "skipped"
                        )
                        update_sub_agent(task_id, _sid, "done", result_summary=_summary)
                    except Exception:
                        pass

            if _skill_response:
                if _TASK_TRACKING:
                    try:
                        _finish_task(task_id)
                    except Exception:
                        pass
                sm = get_session_manager()
                sm.add_message(platform, internal_id, "user", user_text)
                sm.add_message(platform, internal_id, "assistant", _skill_response)
                self.dedupe_cache.set(
                    platform, str(external_chat_id), message_id, _skill_response
                )
                processing_time = (time.time() - start_time) * 1000
                return {
                    "text": _skill_response,
                    "timestamp": datetime.utcnow(),
                    "model_used": _skill_model,
                    "processing_time_ms": round(processing_time, 2),
                }
            # ──────────────────────────────────────────────────────────────────

            # Load user profile and conversation history in parallel
            user_profile, history = await self._batch_load_context(
                internal_id, platform
            )

            # Summarise very long histories to stay within the context window
            history = self._maybe_summarise_history(history)

            # Build structured prompt — internal_id scopes the prompt cache per user
            prompt = self._build_structured_prompt(
                user_profile, history, user_text, internal_id=internal_id
            )

            temperature = self.personality_context.get_response_temperature()

            # Call the best available LLM provider (cloud or local).
            # Pass max_tokens=None so:
            #  - Cloud APIs use their model defaults (generous, no artificial cap).
            #  - The local llama.cpp manager computes the exact available context
            #    window after tokenising the prompt — responses are never truncated.
            response: Optional[str] = None
            _llm_agent_id = "llm_provider"
            if _TASK_TRACKING:
                try:
                    register_sub_agent(
                        task_id,
                        _llm_agent_id,
                        role="llm_inference",
                        description="Running LLM inference (best available provider)",
                    )
                except Exception:
                    pass
            try:
                from llm.providers import ask_best_provider

                response = ask_best_provider(
                    prompt, temperature=temperature, max_tokens=None
                )
            except Exception:
                pass

            # Hard fallback: local llama.cpp (max_tokens=None → fully dynamic)
            if response is None or response.startswith("[Error"):
                response = llm_manager.ask_llm(
                    prompt, max_tokens=None, temperature=temperature
                )

            if _TASK_TRACKING:
                try:
                    update_sub_agent(task_id, _llm_agent_id, "done")
                except Exception:
                    pass

            # Sanitize output
            response = self._sanitize_output(response)
            response = self.personality_context.apply_response_style(
                response,
                user_text,
                user_profile=user_profile,
                history=history,
            )

            # Save to conversation history
            sm = get_session_manager()
            sm.add_message(platform, internal_id, "user", user_text)
            sm.add_message(platform, internal_id, "assistant", response)

            # Proactive learning: extract user preferences from this exchange.
            # Submitted to a bounded thread pool (max 2 workers) so concurrent
            # extractions are capped and the main event-loop thread pool is not starved.
            try:
                from memory.learning import learn_from_exchange

                _LEARNING_EXECUTOR.submit(
                    learn_from_exchange, internal_id, user_text, response
                )
            except Exception:
                pass

            self.dedupe_cache.set(platform, str(external_chat_id), message_id, response)

            if _TASK_TRACKING:
                try:
                    _finish_task(task_id)
                except Exception:
                    pass

            processing_time = (time.time() - start_time) * 1000

            return {
                "text": response,
                "timestamp": datetime.utcnow(),
                "model_used": llm_manager.DEFAULT_LLAMA_MODEL,
                "processing_time_ms": round(processing_time, 2),
            }

        except Exception as e:
            if _TASK_TRACKING:
                try:
                    _finish_task(task_id, status="failed")
                except Exception:
                    pass
            logger.error(f"Error in process_message: {e}", exc_info=True)
            processing_time = (time.time() - start_time) * 1000
            return {
                "text": f"[Error processing message: {str(e)[:100]}]",
                "timestamp": datetime.utcnow(),
                "model_used": "N/A",
                "processing_time_ms": round(processing_time, 2),
            }

    async def _batch_load_context(
        self, internal_id: str, platform: str = "unknown"
    ) -> Tuple[Dict, list]:
        """
        Batch-load user profile and conversation history in parallel.
        History is returned as a list of (role, content) tuples.
        """
        loop = asyncio.get_running_loop()

        user_profile_task = loop.run_in_executor(
            None, UserManager.get_user_profile, internal_id
        )

        def load_history():
            messages = get_session_manager().get_history(platform, internal_id)
            # Enforce a workflow-level cap on history size to avoid unbounded prompts.
            if hasattr(self, "max_history") and self.max_history:
                try:
                    limit = int(self.max_history) * 2
                    if limit > 0:
                        messages_to_use = messages[-limit:]
                    else:
                        messages_to_use = messages
                except (TypeError, ValueError):
                    # If max_history is not a valid integer, fall back to all messages.
                    messages_to_use = messages
            else:
                messages_to_use = messages
            return [(m["role"], m["content"]) for m in messages_to_use]

        history_task = loop.run_in_executor(None, load_history)

        user_profile, history = await asyncio.gather(user_profile_task, history_task)
        return user_profile or {}, history or []

    # History summarisation threshold: summarise when history exceeds this many turns
    _HISTORY_SUMMARISE_THRESHOLD = int(os.getenv("HISTORY_SUMMARISE_THRESHOLD", "20"))
    # Number of recent turns to keep verbatim after summarisation
    _HISTORY_KEEP_RECENT = int(os.getenv("HISTORY_KEEP_RECENT", "6"))

    def _maybe_summarise_history(self, history: list) -> list:
        """
        When the conversation history is very long, compress the older portion
        into a short prose summary so the prompt stays within the context window.

        The most recent ``_HISTORY_KEEP_RECENT`` turns are always kept verbatim.
        If the history is shorter than ``_HISTORY_SUMMARISE_THRESHOLD`` turns, it
        is returned unchanged.

        Returns a (possibly shorter) list of (role, content) tuples.
        """
        threshold = self._HISTORY_SUMMARISE_THRESHOLD
        keep_recent = self._HISTORY_KEEP_RECENT

        if len(history) <= threshold:
            return history

        older = history[:-keep_recent]
        recent = history[-keep_recent:]

        # Build a plain-text rendering of the older portion for summarisation.
        # Truncate at the nearest word boundary and add ellipsis when cut.
        lines = []
        for role, content in older:
            label = "User" if role == "user" else "Assistant"
            if len(content) > _SUMMARY_CONTENT_MAX_LENGTH:
                truncated = (
                    content[:_SUMMARY_CONTENT_MAX_LENGTH].rsplit(" ", 1)[0] + "…"
                )
            else:
                truncated = content
            lines.append(f"{label}: {truncated}")

        summary_prompt = (
            "Summarise the following conversation in 3–5 concise sentences, "
            "capturing the key topics, any important facts the user shared, "
            "and the overall context. Be factual and neutral.\n\n"
            + "\n".join(lines)
            + "\n\nSummary:"
        )

        summary: Optional[str] = None
        try:
            from llm.providers import ask_best_provider  # noqa: PLC0415

            summary = ask_best_provider(summary_prompt, temperature=0.3, max_tokens=200)
        except Exception:
            pass

        if summary is None:
            try:
                summary = llm_manager.ask_llm(
                    summary_prompt, temperature=0.3, max_tokens=200
                )
            except Exception:
                pass

        if summary and not summary.startswith("[Error"):
            logger.debug(
                "Summarised %d older history turns into a context note", len(older)
            )
            summary_entry = (
                "assistant",
                f"[Earlier conversation summary: {summary.strip()}]",
            )
            return [summary_entry] + recent

        # Fallback: just truncate to the recent turns
        return recent

    def _build_structured_prompt(
        self, user_profile: Dict, history: list, user_text: str, internal_id: str = ""
    ) -> str:
        """
        Build prompt using structured chat format.
        internal_id is used to scope the prompt cache so users never share entries.

        The prompt always includes:
        1. Persona / system prompt
        2. [USER CONTEXT] — date, time, timezone, location, key preferences
           This block is always present so the assistant is aware of the user's
           situation even before any facts have been learned from conversation.
        3. [VERIFIED FACTS ABOUT USER] — additional learned profile facts
        4. [CONVERSATION HISTORY]
        5. Current user message
        """
        history_str = "\n".join([f"{role}: {msg[:50]}" for role, msg in history[-5:]])
        personality_directives = self.personality_context.build_prompt_directives(
            user_text,
            user_profile=user_profile,
            history=history,
        )

        # Resolve timezone: prefer learned profile → operator default → UTC
        user_tz = (
            (user_profile.get("timezone") if user_profile else None)
            or _DEFAULT_TIMEZONE
            or "UTC"
        )
        try:
            tz = pytz.timezone(user_tz)
        except (pytz.UnknownTimeZoneError, pytz.AmbiguousTimeError):
            tz = pytz.UTC
            user_tz = "UTC"

        # Get current time from system clock (with background internet verification)
        try:
            from utils.system_time import get_verified_now, get_time_source_label

            now = get_verified_now(tz=tz)
            time_source = get_time_source_label()
        except Exception:
            now = datetime.now(tz)
            time_source = "system clock"

        # Resolve location: prefer learned profile → operator default → unknown
        user_location = (
            (user_profile.get("location") if user_profile else None)
            or _DEFAULT_LOCATION
            or ""
        )

        time_bucket = now.strftime("%Y-%m-%d-%H")

        cached = self.prompt_cache.get(
            self.persona.get("system_prompt", ""),
            user_profile,
            history_str,
            time_bucket,
            internal_id=internal_id,
        )

        if cached:
            base_prompt, _ = cached
        else:
            lines = []

            system_prompt = self.persona.get(
                "system_prompt", "You are a helpful assistant."
            )
            lines.append(system_prompt)

            lines.append("\n[PERSONALITY STATE]")
            lines.extend(personality_directives)

            lines.append("\n[IMPORTANT RULES]")
            lines.append(
                "- Be natural, conversational, and helpful like talking to a friend."
            )
            lines.append(
                "- Be concise but complete - answer questions fully without being overwhelming."
            )
            lines.append("- If you don't know something, just say so naturally.")
            lines.append(
                "- Avoid meta-commentary like 'As an AI...' or '[Note: ...]' - just respond directly."
            )
            lines.append(
                "- Don't include action descriptions like *nods* or *gestures*."
            )

            disallow_code = self.persona.get("disallow_code", False)
            if disallow_code:
                lines.append(
                    "- When discussing technical topics, explain concepts clearly without code examples."
                )
            else:
                lines.append(
                    "- Use code examples when helpful for technical discussions, but explain them in plain language too."
                )

            # Always include user context — even new users get date/time/location awareness
            lines.append("\n[USER CONTEXT]")
            lines.append(f"- Current date: {now.strftime('%A, %B %d, %Y')}")
            lines.append(f"- Current time: {now.strftime('%I:%M %p %Z')}")
            lines.append(f"- Timezone: {user_tz}")
            lines.append(f"- Time source: {time_source}")
            if user_location:
                lines.append(f"- Location: {user_location}")

            # Surface any additional learned facts the user has shared
            if user_profile:
                # Filter out keys already shown in [USER CONTEXT] to avoid duplication
                _context_keys = frozenset({"timezone", "location"})
                extra_relevant = {
                    k: v
                    for k, v in _select_relevant_facts(user_profile, user_text).items()
                    if k not in _context_keys
                }
                if extra_relevant:
                    lines.append("\n[VERIFIED FACTS ABOUT USER]")
                    for key, value in extra_relevant.items():
                        lines.append(f"- {key}: {value}")

            if history:
                lines.append("\n[CONVERSATION HISTORY]")
                for role, msg in history:
                    role_label = "User" if role == "user" else "Assistant"
                    lines.append(f"{role_label}: {msg}")

            base_prompt = "\n".join(lines)

            self.prompt_cache.set(
                self.persona.get("system_prompt", ""),
                user_profile,
                history_str,
                base_prompt,
                len(base_prompt.split()),
                time_bucket,
                internal_id=internal_id,
            )

        prompt_parts = [base_prompt, f"\nUser: {user_text}", "Assistant:"]

        return "\n".join(prompt_parts)

    def _sanitize_output(self, response: str) -> str:
        """Clean output to remove unwanted artifacts."""
        response = self.SPEAKER_TAG_PATTERN.sub("", response).strip()
        response = self.META_NOTE_PATTERN.sub("", response).strip()
        response = self.ACTION_PATTERN.sub("", response).strip()

        if not self.minimal_sanitization:
            response = self.CODE_BLOCK_PATTERN.sub("", response).strip()
            response = self.INLINE_CODE_PATTERN.sub("", response).strip()

        response = re.sub(r" +", " ", response)
        response = re.sub(r"\n\n\n+", "\n\n", response)

        return response.strip()

    def change_persona(self, persona_name: str) -> bool:
        """Switch to a different persona."""
        persona_file = os.path.join(
            os.path.dirname(__file__),
            "..",
            "assets",
            "personality",
            f"{persona_name}.json",
        )
        if os.path.exists(persona_file):
            with open(persona_file) as f:
                self.persona = normalize_persona(json.load(f))
            self.personality_context = PersonalityContext(self.persona)
            self.prompt_cache = PromptCache(max_size=100)
            logger.info(f"Switched to persona: {persona_name}")
            return True
        logger.warning(f"Persona not found: {persona_name}")
        return False

    def get_cache_stats(self) -> Dict:
        """Return cache statistics for monitoring."""
        return {
            "prompt_cache": self.prompt_cache.stats(),
            "dedupe_cache_size": len(self.dedupe_cache.cache),
            "current_persona": self.persona.get("name", "Unknown"),
        }
