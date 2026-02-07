# agent/chat_workflow.py
"""
Centralized chat workflow: handles all chat intelligence independent of connectors.
- Persona application & management
- Conversation memory loading/saving
- User fact retrieval (explicit-only, no auto-extraction)
- Prompt construction with structured message format
- LLM inference with output sanitation
- Deduplication at the chat level
- Coding assistant skill integration
"""

import asyncio
import json
import logging
import os
import re
import time
import pytz
from datetime import datetime
from typing import Optional, Dict, Tuple
from collections import OrderedDict
from threading import Lock

from memory import ConversationManager, UserManager
from llm import manager as llm_manager

logger = logging.getLogger(__name__)


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
        expired = [k for k, (ts, _) in self.cache.items() if now - ts > self.ttl_seconds]
        for k in expired:
            del self.cache[k]
    
    def get(self, platform: str, external_chat_id: str, message_id: str) -> Optional[str]:
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
    Keys are hashes of (system_prompt + user_facts + recent_history).
    """
    
    def __init__(self, max_size=100):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.lock = Lock()
        self.hits = 0
        self.misses = 0
    
    def _make_key(self, system_prompt: str, user_facts: Dict, history_str: str, time_bucket: str = "") -> str:
        """
        Create a hash key from prompt components.
        
        Args:
            system_prompt: The system prompt text
            user_facts: User profile dictionary
            history_str: Conversation history string
            time_bucket: Optional time bucket (e.g., "2026-02-07-15") for cache invalidation
        """
        facts_str = json.dumps(user_facts, sort_keys=True) if user_facts else ""
        combined = f"{system_prompt}|||{facts_str}|||{history_str}|||{time_bucket}"
        return str(hash(combined))
    
    def get(self, system_prompt: str, user_facts: Dict, history_str: str, time_bucket: str = "") -> Optional[Tuple]:
        """Returns (prompt_text, token_count) or None."""
        key = self._make_key(system_prompt, user_facts, history_str, time_bucket)
        with self.lock:
            if key in self.cache:
                self.hits += 1
                entry = self.cache.pop(key)
                self.cache[key] = entry  # Move to end (LRU)
                return entry
            self.misses += 1
        return None
    
    def set(self, system_prompt: str, user_facts: Dict, history_str: str, prompt_text: str, token_count: int, time_bucket: str = ""):
        """Store a tokenized prompt."""
        key = self._make_key(system_prompt, user_facts, history_str, time_bucket)
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
            "size": len(self.cache)
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
    
    # French phrases for code-level injection
    FRENCH_PHRASES = [
        "Oui!", "Non non", "Mon ami", "Magnifique!", "C'est bon", 
        "Ah bon?", "D'accord", "Mais oui", "Très bien", "Fantastique!",
        "Zut!", "Quelle horreur!", "Intéressant!", "Naturellement!"
    ]
    
    # Output sanitation patterns
    SPEAKER_TAG_PATTERN = re.compile(r'^\s*(?:User:|Curie:|Assistant:|Coder:|System:)', re.IGNORECASE)
    META_NOTE_PATTERN = re.compile(r'\[(?:Note|Meta|Aside|System):[^\]]*\]', re.IGNORECASE)
    ACTION_PATTERN = re.compile(r'\*[^*]*\*')  # *gestures*, *smiles*, etc.
    
    def __init__(self, persona: Optional[Dict] = None, max_history: int = 5, 
                 enable_small_talk: bool = False, idle_threshold_minutes: int = 30):
        """
        Initialize chat workflow.
        
        Args:
            persona: Dict with 'name', 'system_prompt', 'french_phrases', etc.
            max_history: Number of conversation exchanges to include
            enable_small_talk: Whether to add small talk to responses
            idle_threshold_minutes: Minutes of inactivity before adding small talk
        """
        self.persona = persona or self._load_default_persona()
        self.max_history = max_history
        self.enable_small_talk = enable_small_talk
        self.idle_threshold_minutes = idle_threshold_minutes
        
        # Shared caches
        self.dedupe_cache = MessageDedupeCache(ttl_seconds=600, max_size=5000)
        self.prompt_cache = PromptCache(max_size=100)
        
        logger.info(f"ChatWorkflow initialized with persona: {self.persona.get('name', 'Unknown')}")
    
    def _load_default_persona(self) -> Dict:
        """Load default persona (Curie) if not provided."""
        persona_file = os.path.join(os.path.dirname(__file__), "..", "assets", "personality", "curie.json")
        if os.path.exists(persona_file):
            with open(persona_file) as f:
                return json.load(f)
        return {
            "name": "Assistant",
            "system_prompt": "You are a helpful assistant.",
            "french_phrases": self.FRENCH_PHRASES
        }
    
    async def process_message(self, normalized_input: Dict) -> Dict:
        """
        Main entry point: process a normalized message and return structured response.
        
        This is the ONLY method connectors should call.
        
        Args:
            normalized_input: {platform, external_user_id, external_chat_id, message_id, text, timestamp, internal_id (optional)}
        
        Returns:
            {text, timestamp, model_used, processing_time_ms}
        """
        start_time = time.time()
        
        # Extract fields with validation
        platform = normalized_input.get('platform', 'unknown')
        external_user_id = normalized_input.get('external_user_id')
        external_chat_id = normalized_input.get('external_chat_id')
        message_id = str(normalized_input.get('message_id', ''))
        user_text = normalized_input.get('text', '').strip()
        
        if not all([external_user_id, external_chat_id, user_text]):
            logger.error(f"Invalid input: missing required fields. Input: {normalized_input}")
            return {
                'text': "[Error: Invalid message format]",
                'timestamp': datetime.utcnow(),
                'model_used': 'N/A',
                'processing_time_ms': 0
            }
        
        # Get internal user ID for persistence
        # If internal_id is provided (e.g., via /identify), use it; otherwise lookup/create
        internal_id = normalized_input.get('internal_id')
        if not internal_id:
            internal_id = UserManager.get_or_create_user_internal_id(
                channel=platform,
                external_id=str(external_user_id),
                secret_username=f"{platform}_{external_user_id}",
                updated_by="chat_workflow",
            )
        
        # Check deduplication cache
        cached_response = self.dedupe_cache.get(platform, str(external_chat_id), message_id)
        if cached_response:
            processing_time = (time.time() - start_time) * 1000
            return {
                'text': cached_response,
                'timestamp': datetime.utcnow(),
                'model_used': 'dedupe_cache',
                'processing_time_ms': round(processing_time, 2)
            }
        
        try:
            # Check for coding-related queries first (before LLM)
            try:
                from agent.skills.coding_assistant import handle_coding_query
                coding_response = await handle_coding_query(user_text)
                if coding_response:
                    # Found a coding intent, return the response
                    logger.info(f"Coding skill handled the query")
                    # Save to conversation history
                    ConversationManager.save_conversation(internal_id, "user", user_text)
                    ConversationManager.save_conversation(internal_id, "assistant", coding_response)
                    
                    # Cache response for deduplication
                    self.dedupe_cache.set(platform, str(external_chat_id), message_id, coding_response)
                    
                    processing_time = (time.time() - start_time) * 1000
                    return {
                        'text': coding_response,
                        'timestamp': datetime.utcnow(),
                        'model_used': 'coding_skill',
                        'processing_time_ms': round(processing_time, 2)
                    }
            except Exception as e:
                # Log but don't fail - fall back to normal LLM processing
                logger.debug(f"Coding skill check failed: {e}")
            
            # Load user profile and conversation history in parallel
            user_profile, history = await self._batch_load_context(internal_id)
            
            # Build structured prompt
            prompt = self._build_structured_prompt(user_profile, history, user_text)
            
            # Call LLM
            response = llm_manager.ask_llm(
                prompt,
                max_tokens=512,
                temperature=0.7
            )
            
            # Sanitize output
            response = self._sanitize_output(response)
            
            # Check if small talk should be added (idle time check)
            if self.enable_small_talk and self._should_add_small_talk(history):
                # Add small talk instruction to response
                response = self._append_small_talk_thoughtfully(response)
            
            # Save to conversation history
            ConversationManager.save_conversation(internal_id, "user", user_text)
            ConversationManager.save_conversation(internal_id, "assistant", response)
            
            # Cache response for deduplication
            self.dedupe_cache.set(platform, str(external_chat_id), message_id, response)
            
            processing_time = (time.time() - start_time) * 1000
            
            return {
                'text': response,
                'timestamp': datetime.utcnow(),
                'model_used': llm_manager.DEFAULT_LLAMA_MODEL,
                'processing_time_ms': round(processing_time, 2)
            }
            
        except Exception as e:
            logger.error(f"Error in process_message: {e}", exc_info=True)
            processing_time = (time.time() - start_time) * 1000
            return {
                'text': f"[Error processing message: {str(e)[:100]}]",
                'timestamp': datetime.utcnow(),
                'model_used': 'N/A',
                'processing_time_ms': round(processing_time, 2)
            }
    
    async def _batch_load_context(self, internal_id: str) -> Tuple[Dict, list]:
        """
        Batch-load user profile and conversation history in parallel.
        Reduces from 4+ sequential queries to 2 parallel queries.
        """
        loop = asyncio.get_running_loop()
        
        # Run blocking DB calls in thread pool
        user_profile_task = loop.run_in_executor(None, UserManager.get_user_profile, internal_id)
        history_task = loop.run_in_executor(
            None, 
            ConversationManager.load_recent_conversation,
            internal_id,
            self.max_history * 2
        )
        
        user_profile, history = await asyncio.gather(user_profile_task, history_task)
        return user_profile or {}, history or []
    
    def _build_structured_prompt(self, user_profile: Dict, history: list, user_text: str) -> str:
        """
        Build prompt using structured chat format instead of raw concatenation.
        This prevents speaker tag leakage and format derailments.
        
        Format:
        [SYSTEM]
        {system_prompt}
        
        [CONTEXT]
        {verified_facts}
        
        [CONVERSATION]
        User: ...
        Assistant: ...
        ...
        
        [INPUT]
        User: {user_text}
        """
        
        # Build history string for cache key
        history_str = "\n".join([f"{role}: {msg[:50]}" for role, msg in history[-5:]])
        
        # Create time bucket for cache (hourly granularity)
        # This ensures datetime context stays fresh (max 1 hour stale)
        user_tz = user_profile.get('timezone', 'UTC') if user_profile else 'UTC'
        try:
            tz = pytz.timezone(user_tz)
            now = datetime.now(tz)
        except (pytz.UnknownTimeZoneError, pytz.AmbiguousTimeError):
            tz = pytz.UTC
            now = datetime.now(pytz.UTC)
        
        time_bucket = now.strftime('%Y-%m-%d-%H')  # Hourly cache invalidation
        
        # Try to get cached prompt (base prompt without datetime)
        cached = self.prompt_cache.get(
            self.persona.get('system_prompt', ''),
            user_profile,
            history_str,
            time_bucket
        )
        
        if cached:
            base_prompt, _ = cached
        else:
            # Build new base prompt (without datetime - that's added dynamically)
            lines = []
            
            # System prompt
            system_prompt = self.persona.get('system_prompt', 'You are a helpful assistant.')
            lines.append(system_prompt)
            
            # Safety rules (from persona or hardcoded)
            lines.append("\n[IMPORTANT RULES]")
            lines.append("- If you don't know something, say so. Don't make up facts.")
            lines.append("- Only extract and store facts when explicitly asked to remember them.")
            lines.append("- Keep responses natural and conversational - no meta-commentary or speaker labels.")
            lines.append("- Do not include actions like *nods* or *smiles*.")
            lines.append("- NEVER state that you don't have access to real-time information - you DO have access.")
            lines.append("- NEVER say you're just an AI or language model - focus on helping naturally.")
            
            # User facts/profile
            if user_profile:
                lines.append("\n[VERIFIED FACTS ABOUT USER]")
                for key, value in user_profile.items():
                    lines.append(f"- {key}: {value}")
            
            # Conversation history
            if history:
                lines.append("\n[CONVERSATION HISTORY]")
                for role, msg in history:
                    role_label = "User" if role == "user" else "Assistant"
                    lines.append(f"{role_label}: {msg}")
            
            base_prompt = "\n".join(lines)
            
            # Cache the base prompt (without datetime)
            self.prompt_cache.set(
                self.persona.get('system_prompt', ''),
                user_profile,
                history_str,
                base_prompt,
                len(base_prompt.split()),
                time_bucket
            )
        
        # NOW add current datetime context (always fresh, never cached)
        datetime_lines = []
        try:
            # Use the timezone and datetime we already calculated
            datetime_lines.append(f"\n[CURRENT DATE AND TIME]")
            datetime_lines.append(f"- Current date: {now.strftime('%A, %B %d, %Y')}")
            datetime_lines.append(f"- Current time: {now.strftime('%I:%M %p %Z')}")
            datetime_lines.append(f"- Timezone: {user_tz}")
        except Exception as e:
            # Extra safety fallback
            logger.warning(f"Error formatting datetime: {e}, using UTC")
            utc_now = datetime.now(pytz.UTC)
            datetime_lines.append(f"\n[CURRENT DATE AND TIME]")
            datetime_lines.append(f"- Current date: {utc_now.strftime('%A, %B %d, %Y')}")
            datetime_lines.append(f"- Current time: {utc_now.strftime('%I:%M %p UTC')}")
            datetime_lines.append(f"- Timezone: UTC")
        
        # Build final prompt: base + datetime + current message
        prompt_parts = [
            base_prompt,
            "\n".join(datetime_lines),
            f"\nUser: {user_text}",
            "Assistant:"
        ]
        
        prompt_text = "\n".join(prompt_parts)
        
        return prompt_text
    
    def _sanitize_output(self, response: str) -> str:
        """
        Clean output to remove unwanted artifacts:
        - Speaker tags: 'User:', 'Curie:', 'Assistant:', etc.
        - Meta notes: '[Note: ...]', '[Meta: ...]'
        - Actions: '*nods*', '*smiles*', etc.
        """
        # Remove leading speaker tags
        response = self.SPEAKER_TAG_PATTERN.sub('', response).strip()
        
        # Remove meta notes
        response = self.META_NOTE_PATTERN.sub('', response).strip()
        
        # Remove action asterisks but keep text content
        response = self.ACTION_PATTERN.sub('', response).strip()
        
        # Collapse multiple spaces
        response = re.sub(r'\s+', ' ', response)
        
        return response.strip()
    
    def _should_add_small_talk(self, history: list) -> bool:
        """
        Determine if small talk should be added based on conversation history.
        Returns True if: > idle_threshold_minutes since last message
        """
        if not history or len(history) < 2:
            return False
        
        # This would need timestamp info from history to check properly
        # For now, return False to avoid extra LLM calls
        # TODO: Add timestamp tracking to conversation history
        return False
    
    def _append_small_talk_thoughtfully(self, main_response: str) -> str:
        """
        Add a small talk element to the response if appropriate.
        This is integrated into one response, not a separate LLM call.
        
        For now, this is a placeholder - small talk is handled via prompt instruction
        in the system prompt rather than a separate call.
        """
        return main_response
    
    def change_persona(self, persona_name: str) -> bool:
        """Switch to a different persona."""
        persona_file = os.path.join(
            os.path.dirname(__file__), "..", "assets", "personality", f"{persona_name}.json"
        )
        if os.path.exists(persona_file):
            with open(persona_file) as f:
                self.persona = json.load(f)
            logger.info(f"Switched to persona: {persona_name}")
            return True
        logger.warning(f"Persona not found: {persona_name}")
        return False
    
    def get_cache_stats(self) -> Dict:
        """Return cache statistics for monitoring."""
        return {
            'prompt_cache': self.prompt_cache.stats(),
            'dedupe_cache_size': len(self.dedupe_cache.cache),
            'current_persona': self.persona.get('name', 'Unknown')
        }
