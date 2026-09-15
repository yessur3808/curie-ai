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
import hashlib
import json
import logging
import os
import re
import time
import uuid
import pytz
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple
from collections import OrderedDict
from threading import Lock

from memory import UserManager
from memory.session_store import get_session_manager
from llm import manager as llm_manager
from agent.personality_context import PersonalityContext
from agent.provenance import response_provenance
from agent.orchestration.response_policy import (  # noqa: F401
    ResponsePolicy,
    naturalize_prose_punctuation as _naturalize_prose_punctuation,
)
from agent.orchestration.model_service import ModelConversationService
from agent.orchestration.learning_service import ConversationLearningService
from agent.observability import (
    RequestTrace,
    latency_metrics,
    operational_metrics,
    turn_event_writer,
)
from agent.kernel.dialogue_state import DialogueStateStore
from agent.kernel.planning import PlanExecutor, build_execution_plan
from agent.kernel.understanding import TurnAnalysis, analyze_turn
from agent.orchestration.session_commands import SessionCommandService
from agent.orchestration.specialist_router import SpecialistRouter
from agent.orchestration.social_service import SocialConversationService
from agent.orchestration.routing_service import UnifiedRoutingService
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
_FACT_STOP_WORDS = frozenset(
    "a an and are as at be by can do for from has have how i in is it me my of on "
    "or that the this to was what when where which who why with you your".split()
)

_FRENCH_FUNCTION_WORDS = frozenset(
    "alors avec avoir bien car ce cette comme dans de des du elle en est et eux "
    "faire il ils je la le les leur lui mais mes mon ne nous ou oui par pas pour "
    "que qui sa se ses si son sur tout très tu une vous voilà votre".split()
)


def _is_predominantly_french(text: str) -> bool:
    """Conservative guard for accidental full-French persona drift."""
    words = re.findall(r"[A-Za-zÀ-ÿ']+", text.lower())
    if len(words) < 8:
        return False
    french_hits = sum(word.strip("'") in _FRENCH_FUNCTION_WORDS for word in words)
    return french_hits >= 4 and french_hits / len(words) >= 0.14


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
    3. Always include only the small set of critical identity facts (name,
       timezone, language, location) regardless of their overlap score.
    4. Return the union of critical facts + top-N scored facts.

    Parameters
    ----------
    user_profile:  Full dict of learned user facts.
    query:         The user's current message.
    top_n:         Maximum number of scored (non-critical) facts to include.
    """
    if not user_profile:
        return {}

    # Only identity and locale are global context. Preferences are retrieved
    # when relevant so an old interest or diet does not hijack a new subject.
    _CRITICAL_KEYS = frozenset(
        {
            "name",
            "preferred_name",
            "timezone",
            "location",
            "language",
        }
    )
    critical = {k: v for k, v in user_profile.items() if k in _CRITICAL_KEYS}

    # Score remaining facts by keyword overlap with the query
    query_words = {
        word
        for word in re.findall(r"[a-z0-9]+", query.casefold())
        if len(word) > 1 and word not in _FACT_STOP_WORDS
    }
    scored = []
    for k, v in user_profile.items():
        if k in _CRITICAL_KEYS:
            continue
        fact_words = set(re.findall(r"[a-z0-9]+", f"{k} {v}".casefold()))
        score = len(query_words & fact_words)
        scored.append((score, k, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_facts = {k: v for score, k, v in scored[:top_n] if score > 0}
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
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.policy = {
            "name": "message_deduplication",
            "owner_scope": "platform and external conversation",
            "ttl_seconds": ttl_seconds,
            "max_size": max_size,
            "invalidation_event": "TTL expiry, connector identity reset, or process restart",
            "sensitivity": "personal",
        }

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
                self.hits += 1
                ts, response = self.cache[key]
                logger.debug(f"Dedupe cache hit: {key}")
                return response
            self.misses += 1
        return None

    def set(self, platform: str, external_chat_id: str, message_id: str, response: str):
        """Store a processed message and its response."""
        key = f"{platform}:{external_chat_id}:{message_id}"
        with self.lock:
            self.cache[key] = (time.time(), response)
            # FIFO eviction when cache exceeds max_size
            while len(self.cache) > self.max_size:
                self.cache.popitem(last=False)
                self.evictions += 1
            logger.debug(f"Dedupe cache set: {key}")

    def stats(self) -> Dict:
        total = self.hits + self.misses
        return {
            **self.policy,
            "size": len(self.cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": round(self.hits / total * 100, 1) if total else 0.0,
            "evictions": self.evictions,
        }


class PromptCache:
    """
    LRU cache for tokenized prompts to avoid repeated tokenization.
    Keys are hashes of (internal_id + system_prompt + user_facts + recent_history).
    internal_id is included so different users never share a cache entry.
    """

    def __init__(self, max_size=100, ttl_seconds=300):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.lock = Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expirations = 0
        self.policy = {
            "name": "personalized_prompts",
            "owner_scope": "internal user identity",
            "ttl_seconds": self.ttl_seconds,
            "max_size": max_size,
            "invalidation_event": "TTL, persona change, profile/history change, or workflow reset",
            "sensitivity": "personal",
        }

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
        return hashlib.sha256(combined.encode()).hexdigest()

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
                entry = self.cache.pop(key)
                if time.monotonic() - entry[0] > self.ttl_seconds:
                    self.expirations += 1
                    self.misses += 1
                    return None
                self.hits += 1
                self.cache[key] = entry  # Move to end (LRU)
                return entry[1:]
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
            self.cache[key] = (time.monotonic(), prompt_text, token_count)
            # Evict oldest if exceeds max
            while len(self.cache) > self.max_size:
                self.cache.popitem(last=False)
                self.evictions += 1

    def stats(self) -> Dict:
        """Return cache hit/miss statistics."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            **self.policy,
            "hits": self.hits,
            "misses": self.misses,
            "total": total,
            "hit_rate_percent": round(hit_rate, 1),
            "size": len(self.cache),
            "evictions": self.evictions,
            "expirations": self.expirations,
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
    # Remove role-play stage directions while preserving Markdown emphasis and
    # bullet lists (the former broad ``*...*`` pattern damaged real answers).
    ACTION_PATTERN = re.compile(
        r"\*(?:(?:I\s+)?(?:smiles?|gestures?|nods?|laughs?|sighs?|shrugs?|"
        r"waves?|blinks?|pauses?|leans?|offers?|gives?|pours?|sits?|stands?))"
        r"[^*\n]*\*",
        re.IGNORECASE,
    )
    THINK_PATTERN = re.compile(
        r"<think>[\s\S]*?</think>|<think>[\s\S]*$", re.IGNORECASE
    )
    CANNED_FRENCH_SUFFIX_PATTERN = re.compile(
        r"\s*\*?(?:c['’]est\s+(?:dommage|magnifique)|très\s+bien)\*?"
        r"(?:,?\s*(?:oui|non))?[?!.]*\s*$",
        re.IGNORECASE,
    )
    CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```|```[\s\S]*$", re.MULTILINE)
    INLINE_CODE_PATTERN = re.compile(r"`[^`]+`")

    def __init__(
        self,
        persona: Optional[Dict] = None,
        max_history: int = 5,
        enable_small_talk: bool = True,
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
        self.response_policy = ResponsePolicy(
            self.persona, self.personality_context, minimal_sanitization
        )
        self.session_commands = SessionCommandService(lambda: get_session_manager())
        self.social_service = SocialConversationService()
        self.specialist_router = SpecialistRouter()
        self.routing_service = UnifiedRoutingService(
            self.social_service, self.specialist_router
        )
        self.model_service = ModelConversationService(llm_manager)
        self.learning_service = ConversationLearningService(_LEARNING_EXECUTOR)
        self.dialogue_state = DialogueStateStore()
        self.plan_executor = PlanExecutor()
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
        """Understand, execute, and trace one complete connector turn."""
        enriched = dict(normalized_input or {})
        original_text = str(enriched.get("text") or "").strip()
        platform = str(enriched.get("platform") or "unknown")
        external_user_id = enriched.get("external_user_id")
        external_chat_id = enriched.get("external_chat_id")
        if not all([external_user_id, external_chat_id, original_text]):
            return await self._process_message_core(enriched)

        internal_id = enriched.get("internal_id")
        if not internal_id:
            internal_id = UserManager.get_or_create_user_internal_id(
                channel=platform,
                external_id=str(external_user_id),
                secret_username=f"{platform}_{external_user_id}",
                updated_by="chat_workflow",
            )
            enriched["internal_id"] = internal_id

        has_device_reference = bool(
            re.search(
                r"\b(?:it|that(?: one| device)?|this(?: one| device)?|them|"
                r"those(?: devices)?|these(?: devices)?|both(?: devices)?|the device)\b",
                original_text,
                re.I,
            )
        )
        has_device_operation = bool(
            re.search(
                r"\b(?:turn|switch|power|status|still (?:on|off)|"
                r"(?:is|are)\b.{0,50}\b(?:on|off|online|offline|running))\b",
                original_text,
                re.I,
            )
        )
        needs_dialogue_history = (
            has_device_reference and has_device_operation
        ) or bool(re.search(r"\btry again\b", original_text, re.I))
        history = (
            get_session_manager().get_history(platform, internal_id)[-8:]
            if needs_dialogue_history
            else []
        )
        resolution = self.dialogue_state.resolve_references(
            original_text,
            platform=platform,
            owner_id=str(internal_id),
            history=history,
        )
        analysis = analyze_turn(
            original_text,
            resolution.resolved_text,
            owner_id=str(internal_id),
            platform=platform,
            history=history,
            resolved_entities=resolution.entities,
        )
        enriched["_effective_text"] = resolution.resolved_text
        enriched["_turn_analysis"] = analysis
        try:
            result = await self._process_message_core(enriched)
        except Exception:
            result = {
                "text": "[Error processing message]",
                "timestamp": datetime.now(timezone.utc),
                "model_used": "N/A",
                "processing_time_ms": 0,
            }
            turn_event_writer.record(analysis.state, result)
            raise
        result["trace_id"] = analysis.state.trace_id
        result["turn_state"] = analysis.state.as_dict()
        result["response_mode"] = analysis.state.response_mode.value
        self.dialogue_state.observe_for_owner(str(internal_id), analysis.state, result)
        turn_event_writer.record(analysis.state, result)
        return result

    async def _process_message_core(self, normalized_input: Dict) -> Dict:
        """
        Main entry point: process a normalized message and return structured response.
        """
        start_time = time.time()
        trace = RequestTrace()

        platform = normalized_input.get("platform", "unknown")
        external_user_id = normalized_input.get("external_user_id")
        external_chat_id = normalized_input.get("external_chat_id")
        message_id = str(normalized_input.get("message_id", ""))
        user_text = normalized_input.get("text", "").strip()
        routing_text = str(normalized_input.get("_effective_text") or user_text).strip()
        turn_analysis = normalized_input.get("_turn_analysis")

        # task_id is always defined so later _finish_task calls are safe even
        # when task tracking is disabled or register_task is called later.
        task_id = str(uuid.uuid4())[:8]

        if not all([external_user_id, external_chat_id, user_text]):
            logger.error(
                f"Invalid input: missing required fields. Input: {normalized_input}"
            )
            return {
                "text": "[Error: Invalid message format]",
                "timestamp": datetime.now(timezone.utc),
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

        operational_signal = normalized_input.get("adaptation_signal")
        if operational_signal:
            try:
                from memory.adaptation import record_operational_signal

                record_operational_signal(
                    str(internal_id),
                    str(operational_signal),
                    tool=str(normalized_input.get("adaptation_tool", "")),
                    latency_ms=normalized_input.get("adaptation_latency_ms"),
                )
            except (TypeError, ValueError) as exc:
                logger.debug("Ignored invalid adaptation signal: %s", exc)

        # Deduplication cache check
        cached_response = self.dedupe_cache.get(
            platform, str(external_chat_id), message_id
        )
        if cached_response:
            processing_time = (time.time() - start_time) * 1000
            return {
                "text": cached_response,
                "timestamp": datetime.now(timezone.utc),
                "model_used": "dedupe_cache",
                "processing_time_ms": round(processing_time, 2),
            }

        # Persist real interaction time so a daemon restart never causes an
        # immediate unsolicited check-in right after the user has messaged.
        try:
            UserManager.update_user_profile(
                internal_id, {"last_user_interaction_at": datetime.now(pytz.UTC)}
            )
        except Exception as exc:
            logger.debug("Could not persist user interaction time: %s", exc)

        try:
            from utils.formatting import rich_format_preview_request

            formatting_preview = rich_format_preview_request(user_text)
            if formatting_preview:
                sm = get_session_manager()
                sm.add_message(platform, internal_id, "user", user_text)
                sm.add_message(platform, internal_id, "assistant", formatting_preview)
                self.dedupe_cache.set(
                    platform, str(external_chat_id), message_id, formatting_preview
                )
                return {
                    "text": formatting_preview,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "deterministic_formatting_preview",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                    "provenance": response_provenance(
                        model_used="deterministic_formatting_preview",
                        user_text=user_text,
                        response_text=formatting_preview,
                    ),
                }
        except Exception as exc:
            logger.debug("Could not render formatting preview: %s", exc)

        try:
            from utils.calculator import calculate_request

            exact_calculation = calculate_request(user_text)
            if exact_calculation:
                sm = get_session_manager()
                sm.add_message(platform, internal_id, "user", user_text)
                sm.add_message(platform, internal_id, "assistant", exact_calculation)
                self.dedupe_cache.set(
                    platform, str(external_chat_id), message_id, exact_calculation
                )
                return {
                    "text": exact_calculation,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "deterministic_calculator",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                    "provenance": response_provenance(
                        model_used="deterministic_calculator",
                        user_text=user_text,
                        response_text=exact_calculation,
                    ),
                }
        except Exception as exc:
            logger.debug("Could not apply deterministic arithmetic: %s", exc)

        # Exact elapsed-time arithmetic is cheap and deterministic. Handle the
        # common split-sleep phrasing before asking a generative model to guess.
        try:
            from utils.time_math import split_sleep_reply

            profile = UserManager.get_user_profile(internal_id) or {}
            timezone_name = profile.get("timezone") or _DEFAULT_TIMEZONE
            exact_reply = split_sleep_reply(
                user_text, datetime.now(pytz.timezone(timezone_name))
            )
            if exact_reply:
                sm = get_session_manager()
                sm.add_message(platform, internal_id, "user", user_text)
                sm.add_message(platform, internal_id, "assistant", exact_reply)
                self.dedupe_cache.set(
                    platform, str(external_chat_id), message_id, exact_reply
                )
                return {
                    "text": exact_reply,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "deterministic_time_math",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                    "provenance": response_provenance(
                        model_used="deterministic_time_math",
                        user_text=user_text,
                        response_text=exact_reply,
                    ),
                }
        except (KeyError, TypeError, ValueError, pytz.UnknownTimeZoneError) as exc:
            logger.debug("Could not apply deterministic time arithmetic: %s", exc)

        # Explicit operational work bypasses personality memory, adaptive recall,
        # prompt construction, and model inference. The typed plan is created by
        # the wrapper before this core path starts, so references and dependencies
        # are stable for the full turn.
        if (
            isinstance(turn_analysis, TurnAnalysis)
            and turn_analysis.operational_decisions
        ):
            try:
                user_profile = UserManager.get_user_profile(internal_id) or {}
                plan = build_execution_plan(
                    turn_analysis.state,
                    turn_analysis.operational_decisions,
                    preserve_order=turn_analysis.preserve_order,
                )

                async def execute_decision(decision):
                    return await self.routing_service.execute(
                        decision,
                        routing_text,
                        str(internal_id),
                        platform,
                        user_profile,
                    )

                with trace.stage("tool"):
                    execution = await self.plan_executor.execute(plan, execute_decision)
                response_parts = [
                    self.response_policy.finalize(
                        outcome.text, user_text, profile=user_profile
                    )
                    for outcome in execution.outcomes
                    if outcome.text.strip()
                ]
                routed_response = "\n\n".join(response_parts)
                model_used = execution.model_used or "operational_plan"
                sm = get_session_manager()
                sm.add_message(platform, internal_id, "user", user_text)
                sm.add_message(platform, internal_id, "assistant", routed_response)
                self.dedupe_cache.set(
                    platform, str(external_chat_id), message_id, routed_response
                )
                timings = trace.finish()
                latency_metrics.observe(timings)
                routing_payload = (
                    turn_analysis.operational_decisions[0].as_dict()
                    if len(turn_analysis.operational_decisions) == 1
                    else {
                        "intent": "compound_operation",
                        "steps": [
                            decision.as_dict()
                            for decision in turn_analysis.operational_decisions
                        ],
                    }
                )
                return {
                    "text": routed_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": model_used,
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                    "timings_ms": timings,
                    "routing": routing_payload,
                    "execution": execution.as_dict(),
                    "execution_status": execution.status,
                    "verification_status": execution.verification_status,
                    "message_parts": response_parts,
                    "provenance": response_provenance(
                        model_used=model_used,
                        user_text=user_text,
                        response_text=routed_response,
                    ),
                }
            except Exception as exc:
                logger.exception("Operational fast-path failed: %s", exc)
                operational_metrics.record_error()
                timings = trace.finish()
                latency_metrics.observe(timings)
                return {
                    "text": "I couldn't complete that command because the operational route failed.",
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "operational_route_error",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                    "timings_ms": timings,
                }

        try:
            from services.personal_ops import handle_personal_ops_command

            personal_response = handle_personal_ops_command(str(internal_id), user_text)
            if personal_response is not None:
                return {
                    "text": personal_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "personal_ops_controls",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
        except Exception as exc:
            logger.debug("Could not process personal operations controls: %s", exc)

        try:
            from services.audit import handle_audit_command

            audit_response = handle_audit_command(str(internal_id), user_text)
            if audit_response is not None:
                return {
                    "text": audit_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "audit_controls",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
        except Exception as exc:
            logger.debug("Could not process audit controls: %s", exc)

        try:
            from services.security import handle_security_command

            security_response = handle_security_command(str(internal_id), user_text)
            if security_response is not None:
                return {
                    "text": security_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "security_privacy_controls",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
        except Exception as exc:
            logger.debug("Could not process security/privacy controls: %s", exc)

        try:
            from services.runtime_health import handle_health_command

            health_response = handle_health_command(user_text, workflow_ready=True)
            if health_response is not None:
                return {
                    "text": health_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "runtime_health",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
        except Exception as exc:
            logger.debug("Could not process health controls: %s", exc)

        try:
            from contracts.catalog import handle_capabilities_command

            capability_response = handle_capabilities_command(user_text)
            if capability_response is not None:
                return {
                    "text": capability_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "capability_discovery",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
        except Exception as exc:
            logger.debug("Could not process capability discovery: %s", exc)

        try:
            from services.proactive_policy import (
                handle_proactive_command,
                mark_user_response,
            )

            proactive_response = handle_proactive_command(str(internal_id), user_text)
            if proactive_response is not None:
                return {
                    "text": proactive_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "proactive_controls",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
            mark_user_response(
                str(internal_id), UserManager.get_user_profile(internal_id) or {}
            )
        except Exception as exc:
            logger.debug("Could not process proactive controls: %s", exc)

        try:
            from memory.adaptive import capture_proactive_feedback

            capture_proactive_feedback(str(internal_id), user_text)
        except Exception as exc:
            logger.debug("Could not persist proactive feedback: %s", exc)

        try:
            from memory.adaptation import (
                apply_voice_modality_preference,
                handle_adaptation_command,
                record_explicit_feedback,
            )

            apply_voice_modality_preference(
                str(internal_id), user_text, str(platform) if platform else None
            )
            adaptation_response = handle_adaptation_command(
                str(internal_id), user_text, str(platform) if platform else None
            )
            if adaptation_response is not None:
                return {
                    "text": adaptation_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "adaptation_controls",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
            record_explicit_feedback(str(internal_id), user_text)
        except Exception as exc:
            logger.debug("Could not process adaptation feedback: %s", exc)

        # ── Per-user session commands ─────────────────────────────────────────
        # Any user can manage their own conversation history.
        # These are handled before the LLM so they never consume tokens.
        try:
            session_response = self.session_commands.handle(
                user_text, platform, str(internal_id)
            )
            if session_response:
                processing_time = (time.time() - start_time) * 1000
                return {
                    "text": session_response.text,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": session_response.model_used,
                    "processing_time_ms": round(processing_time, 2),
                }
        except Exception:
            logger.exception(
                "Error while handling session command '%s' for user %s",
                user_text,
                internal_id,
            )
            processing_time = (time.time() - start_time) * 1000
            return {
                "text": "[Error: Unable to manage conversation history right now. Please try again later.]",
                "timestamp": datetime.now(timezone.utc),
                "model_used": "system",
                "processing_time_ms": round(processing_time, 2),
            }
        # ─────────────────────────────────────────────────────────────────────

        try:
            from agent.task_runtime import handle_task_command

            durable_response = await handle_task_command(
                str(internal_id),
                user_text,
                profile=UserManager.get_user_profile(internal_id) or {},
            )
            if durable_response is not None:
                return {
                    "text": durable_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "durable_task_runtime",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
        except (KeyError, PermissionError, ValueError) as exc:
            return {
                "text": f"Unable to manage that task: {exc}",
                "timestamp": datetime.now(timezone.utc),
                "model_used": "durable_task_runtime",
                "processing_time_ms": round((time.time() - start_time) * 1000, 2),
            }

        # ── Guarded adaptive-learning commands / skill teaching ──────────────
        try:
            from memory.adaptive import (
                handle_adaptive_command,
                propose_learned_ability,
            )

            adaptive_response = handle_adaptive_command(
                internal_id, user_text, str(platform) if platform else None
            )
            if adaptive_response is None:
                proposal = propose_learned_ability(internal_id, user_text)
                if proposal:
                    adaptive_response = (
                        f"I drafted `{proposal['name']}` version {proposal.get('version', 1)} "
                        f"as a {proposal.get('kind')} ability. Trigger: “{proposal['trigger']}”. "
                        f"It will {proposal['procedure']}. Allowed tools: "
                        f"{proposal.get('allowed_tools') or 'none'}; required permissions: "
                        f"{proposal.get('required_permissions') or 'none'}. It has not run. "
                        f"Reply `/approve skill {proposal['name']}` to enable it, or "
                        f"`/reject skill {proposal['name']}` to discard it."
                    )
            if adaptive_response:
                adaptive_response = self.response_policy.finalize(
                    adaptive_response, user_text
                )
                processing_time = (time.time() - start_time) * 1000
                return {
                    "text": adaptive_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": "adaptive_learning",
                    "processing_time_ms": round(processing_time, 2),
                }
        except Exception as exc:
            logger.debug("Adaptive learning command skipped: %s", exc)

        # Typed executable skills never become prompt text. They run only through
        # the registry, which rechecks schemas, permissions, and approval policy.
        executable = None
        try:
            from agent.tooling import ToolContext
            from memory.learned_skills import invoke_skill, matching_skills

            executable = next(
                (
                    skill
                    for skill in matching_skills(str(internal_id), user_text)
                    if skill.get("kind") == "executable_workflow"
                ),
                None,
            )
            if executable:
                permissions = normalized_input.get("permissions")
                context = ToolContext(
                    internal_id=str(internal_id),
                    platform=platform,
                    profile=UserManager.get_user_profile(internal_id) or {},
                    permissions=(
                        frozenset(str(item) for item in permissions)
                        if permissions is not None
                        else frozenset()
                    ),
                    approved=False,
                )
                results = await invoke_skill(executable, {}, context)
                response = (
                    "\n".join(result.text for result in results) or "Skill completed."
                )
                return {
                    "text": self.response_policy.finalize(response, user_text),
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": f"learned_skill:{executable['name']}:v{executable.get('version', 1)}",
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                }
        except PermissionError as exc:
            return {
                "text": f"This learned skill cannot run yet: {exc}",
                "timestamp": datetime.now(timezone.utc),
                "model_used": "learned_skill_policy",
                "processing_time_ms": round((time.time() - start_time) * 1000, 2),
            }
        except Exception as exc:
            logger.exception("Learned skill invocation failed: %s", exc)
            from agent.tooling.errors import user_facing_tool_error

            return {
                "text": user_facing_tool_error(
                    exc,
                    (
                        executable.get("name", "learned skill")
                        if executable
                        else "learned skill"
                    ),
                ),
                "timestamp": datetime.now(timezone.utc),
                "model_used": "learned_skill_error",
                "processing_time_ms": round((time.time() - start_time) * 1000, 2),
            }

        # One schema-validated decision selects normal conversation or exactly
        # one deterministic/social/system/specialist/capability executor.
        try:
            user_profile = UserManager.get_user_profile(internal_id) or {}
            routing_history = get_session_manager().get_history(platform, internal_id)[
                -8:
            ]
            with trace.stage("tool"):
                routing_decision = await self.routing_service.decide(
                    routing_text, str(internal_id), history=routing_history
                )
                routed_candidate = await self.routing_service.execute(
                    routing_decision,
                    routing_text,
                    str(internal_id),
                    platform,
                    user_profile,
                )
            if routed_candidate is not None:
                routed_response = self.response_policy.finalize(
                    routed_candidate.text, user_text, profile=user_profile
                )
                sm = get_session_manager()
                sm.add_message(platform, internal_id, "user", user_text)
                sm.add_message(platform, internal_id, "assistant", routed_response)
                self.dedupe_cache.set(
                    platform, str(external_chat_id), message_id, routed_response
                )
                processing_time = (time.time() - start_time) * 1000
                timings = trace.finish()
                latency_metrics.observe(timings)
                return {
                    "text": routed_response,
                    "timestamp": datetime.now(timezone.utc),
                    "model_used": routed_candidate.model_used,
                    "processing_time_ms": round(processing_time, 2),
                    "timings_ms": timings,
                    "routing": routing_decision.as_dict(),
                    "provenance": response_provenance(
                        model_used=routed_candidate.model_used,
                        user_text=user_text,
                        response_text=routed_response,
                    ),
                }
        except Exception as exc:
            logger.exception("Unified request router failed: %s", exc)
            routing_decision = None

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
            # Load user profile and conversation history in parallel
            with trace.stage("context"):
                user_profile, history = await self._batch_load_context(
                    internal_id, platform
                )

            # Summarise very long histories to stay within the context window
            history = self._maybe_summarise_history(history)

            # Build structured prompt — internal_id scopes the prompt cache per user
            with trace.stage("prompt"):
                prompt = self._build_structured_prompt(
                    user_profile,
                    history,
                    user_text,
                    internal_id=internal_id,
                    turn_analysis=turn_analysis,
                )

            temperature = self.personality_context.get_response_temperature()

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
            queued_at = time.perf_counter()

            async def generate_measured():
                trace.mark("model_queue", queued_at)
                with trace.stage("model_response"):
                    return await self.model_service.generate(
                        prompt,
                        user_text,
                        temperature,
                        owner_id=str(internal_id),
                        request_id=str(message_id),
                    )

            model_candidate = await generate_measured()
            trace.stages_ms["first_token"] = float(
                model_candidate.metadata.get(
                    "first_token_ms", trace.stages_ms["model_response"]
                )
            )
            response = model_candidate.text

            if _TASK_TRACKING:
                try:
                    update_sub_agent(task_id, _llm_agent_id, "done")
                except Exception:
                    pass

            # Sanitize output
            response = self.response_policy.sanitize(response)
            if _is_predominantly_french(response):
                rewrite_prompt = (
                    "Rewrite the answer below primarily in natural English while preserving "
                    "its meaning and Curie's warm scientific personality. Keep English "
                    "dominant and use only short, ordinary French expressions. Do not add facts, commentary, hidden "
                    "reasoning, or role-play catchphrases. Return only the rewritten answer.\n\n"
                    f"Answer to rewrite:\n{response}\n\nRewritten answer:"
                )
                loop = asyncio.get_running_loop()
                rewritten = await loop.run_in_executor(
                    None,
                    lambda: llm_manager.ask_llm(
                        rewrite_prompt,
                        max_tokens=512,
                        temperature=0.2,
                        role="general",
                    ),
                )
                if rewritten and not rewritten.startswith("[Error"):
                    response = self.response_policy.sanitize(rewritten)
            response = self.response_policy.finalize(
                response, user_text, profile=user_profile, history=history
            )

            # Save to conversation history
            sm = get_session_manager()
            sm.add_message(platform, internal_id, "user", user_text)
            sm.add_message(platform, internal_id, "assistant", response)

            # Proactive learning: extract user preferences from this exchange.
            # Submitted to a bounded thread pool (max 2 workers) so concurrent
            # extractions are capped and the main event-loop thread pool is not starved.
            self.learning_service.submit(
                internal_id,
                user_text,
                response,
                source_message_id=message_id,
                source_channel=platform,
            )

            self.dedupe_cache.set(platform, str(external_chat_id), message_id, response)

            if _TASK_TRACKING:
                try:
                    _finish_task(task_id)
                except Exception:
                    pass

            processing_time = (time.time() - start_time) * 1000

            timings = trace.finish()
            latency_metrics.observe(timings)
            from llm.inference_service import get_inference_service

            inference_service = get_inference_service()
            operational_metrics.record_request(
                timings=timings,
                prompt=prompt,
                response=response,
                model=model_candidate.model_used,
                role=str(model_candidate.metadata.get("role", "general")),
                fallback=bool(model_candidate.metadata.get("fallback", False)),
                queue_depth=inference_service.queue_depth,
                inference={
                    **inference_service.snapshot(),
                    **{
                        key: value
                        for key, value in model_candidate.metadata.items()
                        if key
                        in {
                            "request_id",
                            "queue_ms",
                            "first_token_ms",
                            "total_ms",
                            "output_tokens",
                            "tokens_per_second",
                        }
                    },
                    "loaded_models": list(llm_manager.llama_models_cache),
                },
            )
            try:
                from memory.adaptation import record_operational_signal

                record_operational_signal(
                    str(internal_id),
                    "response_time",
                    latency_ms=processing_time,
                )
            except Exception:
                pass
            return {
                "text": response,
                "timestamp": datetime.now(timezone.utc),
                "model_used": model_candidate.model_used,
                "processing_time_ms": round(processing_time, 2),
                "timings_ms": timings,
                "provenance": response_provenance(
                    model_used=model_candidate.model_used,
                    user_text=user_text,
                    response_text=response,
                ),
            }

        except Exception as e:
            operational_metrics.record_error()
            if _TASK_TRACKING:
                try:
                    _finish_task(task_id, status="failed")
                except Exception:
                    pass
            logger.error(f"Error in process_message: {e}", exc_info=True)
            processing_time = (time.time() - start_time) * 1000
            return {
                "text": f"[Error processing message: {str(e)[:100]}]",
                "timestamp": datetime.now(timezone.utc),
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

        def load_context():
            user_profile = dict(UserManager.get_user_profile(internal_id) or {})
            try:
                from memory.adaptation import get_preferences

                user_profile["_adaptation"] = get_preferences(str(internal_id))
            except Exception:
                pass
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
            history = [(m["role"], m["content"]) for m in messages_to_use]
            return user_profile, history

        # These stores are local/cache-backed and bounded. Keeping this atomic
        # avoids inconsistent profile/history snapshots and executor starvation
        # on hosts constrained to a single worker.
        user_profile, history = load_context()
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
        self,
        user_profile: Dict,
        history: list,
        user_text: str,
        internal_id: str = "",
        turn_analysis: TurnAnalysis | None = None,
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
        # This value is part of the prompt-cache key. Keep the full history and
        # current request in it: the old 50-character truncation allowed two
        # different conversations to reuse stale personality state and facts.
        history_str = "\n".join([f"{role}: {msg}" for role, msg in history])
        history_str = f"{history_str}\nCurrent user: {user_text}"
        adaptive_memories: list[dict] = []
        matching_abilities: list[dict] = []
        try:
            from memory.adaptive import (
                get_matching_abilities,
                get_pending_memory_conflicts,
                get_relevant_memories,
            )

            adaptive_memories = get_relevant_memories(internal_id, user_text)
            memory_conflicts = get_pending_memory_conflicts(internal_id)
            matching_abilities = get_matching_abilities(internal_id, user_text)
            adaptive_key = [
                (
                    m.get("key"),
                    m.get("value"),
                    m.get("confirmation_count"),
                    m.get("_memory_tier"),
                    m.get("_relevance"),
                )
                for m in adaptive_memories
            ] + [(a.get("name"), a.get("procedure")) for a in matching_abilities]
            history_str += (
                f"\nAdaptive context: {json.dumps(adaptive_key, default=str)}"
            )
            if memory_conflicts:
                history_str += (
                    "\nUnresolved memory contradictions: "
                    + json.dumps(memory_conflicts, default=str)[:2000]
                    + "\nAsk the user which value is current; do not treat the proposed value as known."
                )
        except Exception as exc:
            logger.debug("Adaptive context unavailable: %s", exc)
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

            if turn_analysis is not None:
                state = turn_analysis.state
                lines.append("\n[CURRENT TURN CONTRACT]")
                lines.append(f"- Response mode: {state.response_mode.value}")
                lines.append(f"- Current goal: {state.goal.intent}")
                lines.append(
                    "- The current goal and newest user message outrank remembered or "
                    "earlier subjects. Mention an older subject only when it is necessary "
                    "to answer this turn."
                )
                if state.response_mode.value == "social":
                    lines.append(
                        "- Treat this as natural conversation. Use light, understated banter "
                        "when it fits, then respond to the actual nuance."
                    )
                    lines.append(
                        "- If the newest message is only a casual quip, answer with one short "
                        "playful sentence. Do not turn it into praise, a plan, or advice."
                    )
                elif state.response_mode.value == "explanation":
                    lines.append(
                        "- Explain the reasoning clearly and proportionately. Lead with the "
                        "answer, then show the useful logic and tradeoffs."
                    )
                else:
                    lines.append(
                        "- Be tactically direct. State the result, blocker, or next required "
                        "choice before any supporting detail."
                    )
                if state.response_mode.value != "social":
                    lines.append(
                        "- Offer at most one proactive recommendation, and only when it is "
                        "specific, grounded in the current context, and materially useful."
                    )
                lines.append(
                    "- When the user explicitly asks you to choose or recommend between "
                    "options, commit to the best option supported by the given facts. State "
                    "one concise assumption if needed instead of handing the choice back."
                )

            lines.append("\n[IMPORTANT RULES]")
            lines.append(
                "- Be natural, conversational, and helpful like talking to a friend."
            )
            lines.append(
                "- Be concise but complete - answer questions fully without being overwhelming."
            )
            lines.append(
                "- For commands, lead with the verified result or blocker. Successful command "
                "replies are usually one or two crisp sentences. Do not begin with 'Certainly', "
                "'Absolutely', 'As requested', or a recap of what the user just asked."
            )
            lines.append(
                "- Respond to the newest request. Do not revive or keep discussing an older "
                "topic unless the user refers to it or it is necessary to answer the request."
            )
            lines.append("- If you don't know something, just say so naturally.")
            lines.append(
                "- Never invent facts, citations, memories, tool results, or completed actions. "
                "Clearly label uncertainty and inference."
            )
            lines.append(
                "- A saved user-context location is only the default. Any place named in the "
                "current request or established as the recent subject overrides it. Never blend "
                "home and destination weather."
            )
            lines.append(
                "- Never claim that a tool is running, promise a later result, or say an action "
                "was started unless the registered tool executor actually ran in this request. "
                "Tool completion or failure must be reported in the current response."
            )
            lines.append(
                "- Return only the user-facing final answer. Never expose hidden reasoning, "
                "scratch work, chain of thought, or <think> tags."
            )
            lines.append(
                "- Write like a natural conversation. Do not use em dashes or semicolons in prose. "
                "Prefer short sentences, commas, and contractions."
            )
            lines.append(
                "- Use the user's name sparingly, only when it adds clarity or warmth. "
                "Do not address them by name in routine replies."
            )
            lines.append(
                "- Use short paragraphs. Add a heading or bullets only when they make a longer "
                "or multi-part answer easier to scan. Do not over-format casual chat."
            )
            lines.append(
                "- When formatting improves clarity, use portable Markdown: **bold**, *italic*, "
                "~~strikethrough~~, ++underline++, `inline code`, [label](https://example.com), "
                "bullets, numbered lists, and compact Markdown tables. Split distinct sections "
                "with a blank line so chat connectors can deliver them as readable messages."
            )
            if str(self.persona.get("name", "")).casefold() == "curie":
                lines.append(
                    "- In sustained casual conversation, respond to the newest nuance, connect "
                    "it naturally to earlier parts of the thread, and offer a real perspective. "
                    "Do not collapse an ongoing conversation into a one-line greeting."
                )
                lines.append(
                    "- When casually discussing project ideas, be an engaged thought partner. "
                    "Explore motives, possibilities, and tradeoffs conversationally. Ask a natural "
                    "follow-up when it genuinely advances the idea, and do not force a formal plan "
                    "or code change until the user asks for one."
                )
                lines.append(
                    "- Keep Curie's warmth like a good, trusted friend: relaxed, attentive, candid, "
                    "and occasionally playful. Do not perform intimacy, overuse pet names, or imply "
                    "human embodiment, exclusivity, dependency, jealousy, guilt, or a need for the "
                    "user to keep responding."
                )
            lines.append(
                "- Be candid and proportionate. Avoid ceremonial apologies, generic disclaimers, "
                "and formal customer-service language."
            )
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
                _context_keys = frozenset(
                    {
                        "timezone",
                        "location",
                        "last_user_interaction_at",
                        "last_proactive_at",
                        "last_proactive_generation_at",
                        "proactive_count_date",
                        "proactive_count_today",
                    }
                )
                extra_relevant = {
                    k: v
                    for k, v in _select_relevant_facts(user_profile, user_text).items()
                    if k not in _context_keys
                }
                if extra_relevant:
                    lines.append("\n[VERIFIED FACTS ABOUT USER]")
                    for key, value in extra_relevant.items():
                        lines.append(f"- {key}: {value}")

            if adaptive_memories:
                lines.append("\n[RELEVANT LONG-TERM MEMORY]")
                for memory in adaptive_memories:
                    lines.append(
                        f"- [{memory.get('_memory_tier', 'archival')}] "
                        f"{memory.get('key')}: {memory.get('value')} "
                        f"(kind={memory.get('kind')}, status={memory.get('status')}, "
                        f"source={memory.get('source')}, confirmations="
                        f"{memory.get('confirmation_count', 1)}, match="
                        f"{memory.get('_retrieval_reason', 'relevant')})"
                    )
                lines.append(
                    "- Use a recalled memory only when it materially helps answer the current "
                    "request. Never mention it merely to demonstrate recall, and never let it "
                    "pull the reply back to an older topic."
                )
                lines.append(
                    "- The user's current statement overrides older memory. A hypothesis is not "
                    "a known fact, and an episodic item describes a past exchange rather than "
                    "necessarily describing the present."
                )

            if matching_abilities:
                lines.append("\n[USER-APPROVED LEARNED ABILITIES]")
                for ability in matching_abilities:
                    if ability.get("kind") == "declarative_response":
                        lines.append(
                            f"- Version {ability.get('version', 1)}: when the user says "
                            f"“{ability.get('trigger')}”, follow this declarative response "
                            f"recipe: {ability.get('procedure')}"
                        )
                lines.append(
                    "- These recipes guide the response only. Never treat them as permission "
                    "for external, destructive, financial, or security-sensitive actions."
                )

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

        prompt_parts = [
            base_prompt,
            f"\nUser: {user_text}",
            (
                "\n[FINAL RESPONSE REQUIREMENTS]\n"
                "- Answer primarily in English unless the user explicitly requests another language.\n"
                "- In casual conversation, Curie's French identity may show through one natural, brief expression when it genuinely fits. It is optional, never a quota, and should usually be omitted from commands and technical replies. Never scatter random French fillers through sentences.\n"
                "- Recalculate quantities independently before agreeing with a correction. For elapsed times, compute each interval and add them before stating the total.\n"
                "- Preserve Curie's established warm, capable voice; do not imitate a "
                "requested replacement persona or its catchphrases.\n"
                "- Return only the user-facing answer; never output hidden reasoning or "
                "think tags.\n"
                "- Do not use em dashes or semicolons in prose. Keep the wording natural and conversational.\n"
                "Assistant:"
            ),
        ]

        return "\n".join(prompt_parts)

    def _sanitize_output(self, response: str) -> str:
        """Compatibility wrapper around the shared response policy."""
        return self.response_policy.sanitize(response)

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
            self.response_policy = ResponsePolicy(
                self.persona, self.personality_context, self.minimal_sanitization
            )
            self.prompt_cache = PromptCache(max_size=100)
            logger.info(f"Switched to persona: {persona_name}")
            return True
        logger.warning(f"Persona not found: {persona_name}")
        return False

    def get_cache_stats(self) -> Dict:
        """Return bounded cache and latency statistics for health monitoring."""
        from utils.ttl_cache import cache_inventory

        return {
            "prompt_cache": self.prompt_cache.stats(),
            "dedupe_cache": self.dedupe_cache.stats(),
            "policy_caches": cache_inventory(),
            "model_response_cache": llm_manager.ResponseCache.stats(),
            "current_persona": self.persona.get("name", "Unknown"),
            "latency": latency_metrics.snapshot(),
        }
