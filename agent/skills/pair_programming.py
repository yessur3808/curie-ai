# agent/skills/pair_programming.py

"""
Pair Programming Skill - Real-time code collaboration
Enables collaborative coding sessions with context sharing and live assistance
"""

import os
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PairProgrammingSession:
    """Manages a pair programming session with context and history"""
    
    def __init__(self, session_id: str, user_id: str):
        self.session_id = session_id
        self.user_id = user_id
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.context = {
            'files': [],
            'current_file': None,
            'language': None,
            'task': None,
            'history': []
        }
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def add_file_context(self, filepath: str, content: str = None):
        """Add a file to the session context"""
        self.context['files'].append({
            'path': filepath,
            'content': content,
            'added_at': datetime.now()
        })
        self.update_activity()
    
    def set_current_file(self, filepath: str):
        """Set the file currently being worked on"""
        self.context['current_file'] = filepath
        self.update_activity()
    
    def add_history_entry(self, action: str, details: str):
        """Add an entry to session history"""
        self.context['history'].append({
            'timestamp': datetime.now(),
            'action': action,
            'details': details
        })
        self.update_activity()
    
    def is_active(self, timeout_minutes: int = 30) -> bool:
        """Check if session is still active"""
        timeout = timedelta(minutes=timeout_minutes)
        return datetime.now() - self.last_activity < timeout
    
    def get_context_summary(self) -> str:
        """Get a summary of the current session context"""
        files_list = "\n".join([f"- {f['path']}" for f in self.context['files']])
        current = self.context['current_file'] or "None"
        task = self.context['task'] or "Not specified"
        
        return (
            f"**Session Context:**\n"
            f"- Task: {task}\n"
            f"- Current File: {current}\n"
            f"- Files in Context:\n{files_list if files_list else '  (none)'}\n"
            f"- Language: {self.context['language'] or 'Not detected'}\n"
            f"- Duration: {self._format_duration()}"
        )
    
    def _format_duration(self) -> str:
        """Format session duration"""
        duration = datetime.now() - self.created_at
        minutes = int(duration.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes} minutes"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"


class PairProgramming:
    """
    Pair Programming skill for real-time code collaboration
    Provides context-aware coding assistance and session management
    """
    
    def __init__(self):
        self.sessions: Dict[str, PairProgrammingSession] = {}
        self.session_timeout_minutes = int(os.getenv('PAIR_PROGRAMMING_TIMEOUT', '30'))
    
    def start_session(self, user_id: str, task: Optional[str] = None) -> str:
        """
        Start a new pair programming session
        
        Args:
            user_id: User identifier
            task: Optional task description
            
        Returns:
            Session information message
        """
        # Clean up old sessions
        self._cleanup_inactive_sessions()
        
        # Check if user already has an active session
        existing = self._get_user_session(user_id)
        if existing:
            return (
                f"You already have an active pair programming session!\n\n"
                f"{existing.get_context_summary()}\n\n"
                f"Use 'end session' to close it or continue working."
            )
        
        # Create new session
        session_id = f"{user_id}_{int(datetime.now().timestamp())}"
        session = PairProgrammingSession(session_id, user_id)
        
        if task:
            session.context['task'] = task
        
        self.sessions[session_id] = session
        
        logger.info(f"Started pair programming session {session_id} for user {user_id}")
        
        return (
            f"🤝 **Pair Programming Session Started!**\n\n"
            f"Session ID: `{session_id}`\n"
            f"Task: {task or 'Not specified'}\n\n"
            f"**What's next?**\n"
            f"- Add files: \"Add file path/to/file.py\"\n"
            f"- Set current file: \"Working on file.py\"\n"
            f"- Ask for help: \"How do I implement X?\"\n"
            f"- Review code: \"Review my code\"\n"
            f"- End session: \"End session\"\n\n"
            f"I'm here to help you code! Let's work together."
        )
    
    def end_session(self, user_id: str) -> str:
        """
        End the user's pair programming session
        
        Args:
            user_id: User identifier
            
        Returns:
            Session summary message
        """
        session = self._get_user_session(user_id)
        if not session:
            return "You don't have an active pair programming session."
        
        # Generate session summary
        duration = session._format_duration()
        files_count = len(session.context['files'])
        actions_count = len(session.context['history'])
        
        summary = (
            f"🎯 **Pair Programming Session Ended**\n\n"
            f"**Session Summary:**\n"
            f"- Duration: {duration}\n"
            f"- Files worked on: {files_count}\n"
            f"- Actions performed: {actions_count}\n"
            f"- Task: {session.context['task'] or 'Not specified'}\n\n"
            f"Great work! Feel free to start a new session anytime."
        )
        
        # Remove session
        del self.sessions[session.session_id]
        logger.info(f"Ended pair programming session {session.session_id}")
        
        return summary
    
    def add_file_to_session(self, user_id: str, filepath: str) -> str:
        """
        Add a file to the current session
        
        Args:
            user_id: User identifier
            filepath: Path to the file
            
        Returns:
            Confirmation message
        """
        session = self._get_user_session(user_id)
        if not session:
            return "No active pair programming session. Start one with 'start pair programming'."
        
        session.add_file_context(filepath)
        session.add_history_entry('add_file', filepath)
        
        return (
            f"✅ Added `{filepath}` to the session context.\n"
            f"Files in session: {len(session.context['files'])}"
        )
    
    def get_session_status(self, user_id: str) -> str:
        """
        Get the current session status
        
        Args:
            user_id: User identifier
            
        Returns:
            Session status message
        """
        session = self._get_user_session(user_id)
        if not session:
            return (
                "No active pair programming session.\n"
                "Start one with: 'start pair programming [task description]'"
            )
        
        return f"🤝 **Active Pair Programming Session**\n\n{session.get_context_summary()}"
    
    def provide_coding_help(self, user_id: str, question: str) -> str:
        """
        Provide context-aware coding help
        
        Args:
            user_id: User identifier
            question: User's question
            
        Returns:
            Helpful response with context
        """
        session = self._get_user_session(user_id)
        if not session:
            return (
                "💡 I can provide better help in a pair programming session!\n"
                "Start one with: 'start pair programming'"
            )
        
        session.add_history_entry('question', question)
        
        # Build context-aware response
        context_info = ""
        if session.context['current_file']:
            context_info = f"\n\nContext: Working on `{session.context['current_file']}`"
        if session.context['language']:
            context_info += f" ({session.context['language']})"
        
        return (
            f"💡 **Coding Assistance**\n\n"
            f"Question: {question}{context_info}\n\n"
            f"I'm analyzing your question with the current session context. "
            f"Let me help you solve this!"
        )
    
    def _get_user_session(self, user_id: str) -> Optional[PairProgrammingSession]:
        """Get active session for a user"""
        for session in self.sessions.values():
            if session.user_id == user_id and session.is_active(self.session_timeout_minutes):
                return session
        return None
    
    def _cleanup_inactive_sessions(self):
        """Remove inactive sessions"""
        inactive = [
            sid for sid, session in self.sessions.items()
            if not session.is_active(self.session_timeout_minutes)
        ]
        for sid in inactive:
            logger.info(f"Removing inactive session {sid}")
            del self.sessions[sid]


# Global instance
_pair_programming = None


def get_pair_programming() -> PairProgramming:
    """Get or create the global pair programming instance"""
    global _pair_programming
    if _pair_programming is None:
        _pair_programming = PairProgramming()
    return _pair_programming
