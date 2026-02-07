# services/proactive_messaging.py
"""
Proactive Messaging Service - Randomly checks in with users like a caring friend.

This service:
1. Schedules random check-ins throughout the day/week
2. Generates contextual, caring messages
3. Respects user preferences and busy status
4. Integrates with all connectors (Telegram, Discord, WhatsApp, API)
"""

import asyncio
import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Dict

from memory import UserManager, ConversationManager
from llm import manager

logger = logging.getLogger(__name__)


class ProactiveMessagingService:
    """
    Service that sends proactive, caring check-in messages to users.
    """
    
    # Maximum entries to keep in memory (prevents unbounded growth)
    # Memory estimate: ~100 bytes per entry = ~100KB for 1000 entries
    # This limit should be sufficient for most deployments while preventing memory issues
    MAX_CONTACT_HISTORY = 1000
    
    # Probability of sending a proactive message when interval is met (30%)
    # This adds randomness to avoid predictable patterns
    PROACTIVE_MESSAGE_PROBABILITY = 0.3
    
    def __init__(self, agent, connectors: Dict = None):
        """
        Initialize the proactive messaging service.
        
        Args:
            agent: The Agent instance with persona
            connectors: Dict mapping platform names to connector instances
        """
        self.agent = agent
        self.connectors = connectors or {}
        self.running = False
        self.thread = None
        self.check_interval = 3600  # Check every hour
        
        # Tracking when we last messaged each user
        # Note: In production, this should be persisted to database
        # For now, using in-memory cache with size limit
        self.last_contact = {}
        self.last_contact_lock = threading.Lock()  # Thread-safe access
        
        logger.info("ProactiveMessagingService initialized")
    
    def start(self):
        """Start the proactive messaging service in a background thread."""
        if self.running:
            logger.warning("ProactiveMessagingService already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_service, daemon=True)
        self.thread.start()
        logger.info("✅ ProactiveMessagingService started")
    
    def stop(self):
        """Stop the proactive messaging service."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("ProactiveMessagingService stopped")
    
    def _run_service(self):
        """Main service loop that runs in background thread."""
        logger.info("ProactiveMessagingService loop started")
        
        while self.running:
            try:
                # Run async check in sync thread
                asyncio.run(self._check_and_send_messages())
            except Exception as e:
                logger.error(f"Error in proactive messaging loop: {e}", exc_info=True)
            
            # Wait before next check
            time.sleep(self.check_interval)
    
    async def _check_and_send_messages(self):
        """Check all users and send proactive messages if appropriate."""
        try:
            # Clean up old contact history periodically
            self._cleanup_old_contacts()
            
            # Get all users who have the proactive_messaging preference enabled
            # For now, we'll check users from recent conversations
            users = self._get_eligible_users()
            
            for user_info in users:
                try:
                    await self._maybe_send_proactive_message(user_info)
                except Exception as e:
                    logger.error(f"Error sending proactive message to user {user_info.get('internal_id')}: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"Error checking users for proactive messaging: {e}", exc_info=True)
    
    def _get_eligible_users(self):
        """
        Get list of users eligible for proactive messaging.
        Returns list of dicts with user info.
        
        Queries MongoDB for users with proactive_messaging_enabled=true
        and joins with PostgreSQL to get platform-specific IDs.
        
        Returns:
            List[Dict]: Each dict contains:
                - internal_id: UUID string
                - platform: 'telegram', 'discord', 'whatsapp', or 'api'
                - external_user_id: Platform-specific user ID
                - proactive_interval_hours: User's preferred interval (default: 24)
                - timezone: User's timezone (default: 'UTC')
                - busy: User's busy status (default: False)
        """
        try:
            from memory.database import mongo_db, get_pg_conn
            
            # Query MongoDB for users with proactive messaging enabled
            cursor = mongo_db.user_profiles.find({
                "facts.proactive_messaging_enabled": True
            })
            
            eligible_users = []
            
            for profile in cursor:
                internal_id = profile.get("_id")
                if not internal_id:
                    continue
                
                facts = profile.get("facts", {})
                
                # Skip if user is busy
                if facts.get("busy", False):
                    continue
                
                # Get user from PostgreSQL to find their platform ID
                try:
                    with get_pg_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT * FROM users WHERE internal_id = %s", (str(internal_id),))
                        user_row = cur.fetchone()
                        
                        if not user_row:
                            logger.warning(f"User {internal_id} found in MongoDB but not in PostgreSQL")
                            continue
                        
                        # Determine which platform this user uses
                        platform = None
                        external_user_id = None
                        
                        if user_row.get('telegram_id'):
                            platform = 'telegram'
                            external_user_id = user_row['telegram_id']
                        elif user_row.get('discord_id'):
                            platform = 'discord'
                            external_user_id = user_row['discord_id']
                        elif user_row.get('whatsapp_id'):
                            platform = 'whatsapp'
                            external_user_id = user_row['whatsapp_id']
                        elif user_row.get('api_id'):
                            platform = 'api'
                            external_user_id = user_row['api_id']
                        
                        if not platform or not external_user_id:
                            logger.warning(f"User {internal_id} has no platform ID")
                            continue
                        
                        # Build user info dict
                        user_info = {
                            'internal_id': internal_id,
                            'platform': platform,
                            'external_user_id': external_user_id,
                            'proactive_interval_hours': facts.get('proactive_interval_hours', 24),
                            'timezone': facts.get('timezone', 'UTC'),
                            'busy': facts.get('busy', False)
                        }
                        
                        eligible_users.append(user_info)
                        
                except Exception as e:
                    logger.error(f"Error querying user {internal_id} from PostgreSQL: {e}")
                    continue
            
            logger.info(f"Found {len(eligible_users)} users eligible for proactive messaging")
            return eligible_users
            
        except Exception as e:
            logger.error(f"Error querying eligible users: {e}", exc_info=True)
            return []
    
    def _cleanup_old_contacts(self):
        """
        Clean up old entries from last_contact cache to prevent memory growth.
        Keeps only the most recent MAX_CONTACT_HISTORY entries.
        """
        with self.last_contact_lock:
            if len(self.last_contact) > self.MAX_CONTACT_HISTORY:
                # Sort by timestamp and keep only recent entries
                sorted_contacts = sorted(
                    self.last_contact.items(),
                    key=lambda x: x[1],
                    reverse=True
                )
                self.last_contact = dict(sorted_contacts[:self.MAX_CONTACT_HISTORY])
                logger.info(f"Cleaned up contact history, kept {self.MAX_CONTACT_HISTORY} most recent entries")
    
    async def _maybe_send_proactive_message(self, user_info: Dict):
        """
        Decide if we should send a proactive message to this user.
        
        Args:
            user_info: Dict with 'internal_id', 'platform', 'external_user_id', etc.
        """
        internal_id = user_info.get('internal_id')
        if not internal_id:
            return
        
        # Check user preferences
        user_profile = UserManager.get_user_profile(internal_id) or {}
        
        # Skip if user has disabled proactive messaging
        if not user_profile.get('proactive_messaging_enabled', False):
            return
        
        # Skip if user is marked as busy
        if user_profile.get('busy', False):
            logger.debug(f"User {internal_id} is busy, skipping proactive message")
            return
        
        # Get last conversation time (thread-safe)
        with self.last_contact_lock:
            last_contact_time = self.last_contact.get(internal_id)
        now = datetime.now(timezone.utc)  # Use timezone-aware datetime
        
        # Get user's preferred check-in interval (in hours)
        min_interval_hours = user_profile.get('proactive_interval_hours', 24)
        
        # Check if enough time has passed
        if last_contact_time:
            hours_since_contact = (now - last_contact_time).total_seconds() / 3600
            if hours_since_contact < min_interval_hours:
                logger.debug(f"Too soon to contact user {internal_id} ({hours_since_contact:.1f}h < {min_interval_hours}h)")
                return
        
        # Randomly decide whether to send (using class constant)
        if random.random() > self.PROACTIVE_MESSAGE_PROBABILITY:
            logger.debug(f"Random check skipped proactive message for user {internal_id}")
            return
        
        # Generate and send message
        message = await self._generate_proactive_message(internal_id)
        
        # Send via appropriate connector
        platform = user_info.get('platform')
        external_user_id = user_info.get('external_user_id')
        
        if platform and external_user_id and platform in self.connectors:
            connector = self.connectors[platform]
            success = await self._send_via_connector(connector, external_user_id, message)
            
            if success:
                # Update last contact time (thread-safe)
                with self.last_contact_lock:
                    self.last_contact[internal_id] = now
                # Save to conversation history
                ConversationManager.save_conversation(internal_id, "assistant", message)
                logger.info(f"✅ Sent proactive message to user {internal_id} on {platform}")
            else:
                logger.warning(f"Failed to send proactive message to user {internal_id} on {platform}")
    
    async def _generate_proactive_message(self, internal_id: str) -> str:
        """
        Generate a natural, caring check-in message.
        
        Args:
            internal_id: User's internal ID
        
        Returns:
            Generated message text
        """
        # Load user profile and recent history
        user_profile = UserManager.get_user_profile(internal_id) or {}
        history = ConversationManager.load_recent_conversation(internal_id, limit=6)
        
        # Build context for LLM
        persona = self.agent.persona
        prompt = (
            f"{persona.get('system_prompt', '')}\n\n"
            "You are reaching out to a friend naturally and casually. "
            "Generate a brief, warm check-in message (1-2 sentences max). "
            "Be genuine, casual, and friendly like texting a friend. "
            "DON'T be formal or overwhelming. DON'T offer multiple options. "
            "DON'T say things like 'Would you like me to...' or 'What would you like me to do?'. "
            "Just be natural and caring. Examples: 'Hey! How's your day going?', "
            "'Thought of you today, hope you're doing well!', 'What's new with you?'\n"
            "CRITICAL: Do NOT reveal you're checking in automatically. Just be natural, be a real genuine, caring friend.\n"
            "Keep it SHORT and CASUAL - 1-2 sentences only!\n"
        )
        
        if user_profile:
            prompt += "\nWhat you know about them:\n"
            for k, v in user_profile.items():
                if k not in ['_id', 'internal_id', 'busy', 'proactive_messaging_enabled', 'proactive_interval_hours']:
                    prompt += f"- {k}: {v}\n"
        
        if history:
            prompt += "\nRecent conversation snippets:\n"
            # Last 3 messages (could be mix of user and assistant)
            for role, msg in history[-3:]:
                prompt += f"{role.capitalize()}: {msg[:100]}...\n"
        
        prompt += "\nYour casual, friendly check-in (1-2 sentences max):"
        
        # Generate message
        message = await asyncio.to_thread(
            manager.ask_llm,
            prompt,
            temperature=0.9,  # Higher temperature for more natural variation
            max_tokens=100  # Reduced to ensure brevity
        )
        
        return message.strip()
    
    async def _send_via_connector(self, connector, external_user_id: str, message: str) -> bool:
        """
        Send message via the appropriate connector.
        
        Args:
            connector: The connector instance
            external_user_id: Platform-specific user ID
            message: Message to send
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Different connectors may have different outbound message interfaces.
            # We support a small set of duck-typed options here so proactive sends
            # can work even if a connector doesn't expose `send_message` directly.

            async def _call_maybe_async(func, *args, **kwargs):
                """Call `func` which may be sync or async, returning after it completes."""
                # If it's declared as a coroutine function, call and await it.
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                # If calling it returns a coroutine, await that.
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                # Otherwise, offload the sync function to a thread.
                return await asyncio.to_thread(func, *args, **kwargs)

            # 1. Preferred interface: `send_message(external_user_id, message)`
            if hasattr(connector, "send_message"):
                await _call_maybe_async(connector.send_message, external_user_id, message)
                return True

            # 2. Common generic interfaces on some connectors: `send` or `send_text`
            if hasattr(connector, "send"):
                await _call_maybe_async(connector.send, external_user_id, message)
                return True

            if hasattr(connector, "send_text"):
                await _call_maybe_async(connector.send_text, external_user_id, message)
                return True

            # 3. Fallback: treat the connector itself as a callable sender.
            if callable(connector):
                await _call_maybe_async(connector, external_user_id, message)
                return True

            # If we reach here, we don't know how to send via this connector.
            logger.warning(
                "Connector %s does not implement a supported outbound messaging interface "
                "(expected one of: send_message, send, send_text, or a callable).",
                type(connector).__name__,
            )
            return False
        except Exception as e:
            logger.error(f"Error sending message via connector: {e}", exc_info=True)
            return False
