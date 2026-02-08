# agent/skills/coding_assistant.py

"""
Coding Assistant Skill - Allows users to interact with coding service through chat
Handles queries about code changes, reviews, updates, and enhancements
"""

import os
import logging
from typing import Optional
import re

logger = logging.getLogger(__name__)


class CodingAssistant:
    """
    Provides conversational interface to coding capabilities
    Users can ask about code changes, request reviews, and get status updates
    """
    
    def __init__(self):
        """Initialize the coding assistant"""
        self.coding_service = None
        self._initialize_service()
    
    def _initialize_service(self):
        """Lazily initialize the coding service if available"""
        try:
            from services.coding_service import CodingService
            # Check if coding service should be available
            if os.getenv('RUN_CODING_SERVICE', 'false').lower() == 'true':
                logger.info("Coding assistant: service integration enabled")
            else:
                logger.debug("Coding assistant: service integration disabled (RUN_CODING_SERVICE not set)")
        except ImportError as e:
            logger.debug(f"Coding assistant: service not available ({e})")
    
    def detect_coding_intent(self, message: str) -> Optional[str]:
        """
        Detect if the user is asking about coding-related topics
        
        Returns:
            - 'review': User wants code review
            - 'status': User wants to know coding service status
            - 'update': User wants code updates/changes
            - 'self_update': User wants to update the system
            - 'info': User wants information about code changes
            - 'git_op': User wants to perform git operations
            - 'edit_file': User wants to edit a file
            - 'pair_programming': User wants to start pair programming
            - 'bug_detection': User wants to detect bugs
            - 'performance_analysis': User wants performance analysis
            - 'code_generation': User wants code generation
            - None: Not a coding-related query
        """
        message_lower = message.lower()
        
        # Git operations (commit, push, pull, fetch, cherry-pick, add)
        git_op_patterns = [
            r'\b(git\s+)?(commit|push|pull|fetch|add|cherry[- ]?pick)\b',
            r'\bstage\s+(files?|changes)\b',
            r'\bpush\s+(to\s+)?(github|remote|origin)\b',
            r'\bpull\s+(from\s+)?(github|remote|origin)\b',
            r'\bfetch\s+(from\s+)?(github|remote|origin)\b',
            r'\bcreate\s+(a\s+)?branch\b',
            r'\bcheckout\s+branch\b',
            r'\bgit\s+status\b',
            r'\bshow\s+(git\s+)?diff\b'
        ]
        
        for pattern in git_op_patterns:
            if re.search(pattern, message_lower):
                return 'git_op'
        
        # File editing operations
        edit_file_patterns = [
            r'\bedit\s+(the\s+)?file\b',
            r'\bmodify\s+(the\s+)?file\b',
            r'\bchange\s+(the\s+)?file\b',
            r'\bwrite\s+to\s+(the\s+)?file\b',
            r'\b(create|delete|remove)\s+(a\s+)?file\b',
            r'\bupdate\s+.*\.py\b',  # Mentions specific file extensions
            r'\bupdate\s+.*\.js\b',
            r'\breplace\s+.*in\s+file\b'
        ]
        
        for pattern in edit_file_patterns:
            if re.search(pattern, message_lower):
                return 'edit_file'
        
        # Review requests
        review_patterns = [
            r'\b(review|check|analyze|inspect)\s+(code|pr|pull request|mr|merge request)',
            r'\bcode\s+review\b',
            r'\breview\s+(my|the|this)\s+code\b',
            r'\bcan\s+you\s+review\b'
        ]
        
        for pattern in review_patterns:
            if re.search(pattern, message_lower):
                return 'review'
        
        # Status queries
        status_patterns = [
            r'\bcoding\s+(service|status)\b',
            r'\b(what|show|tell)\s+(me\s+)?(the\s+)?status\b.*\bcod(e|ing)',
            r'\bhow\s+(is|are)\s+(the\s+)?coding',
            r'\bavailable\s+platforms?\b',
            r'\bwhat\s+platforms\s+are\s+supported'
        ]
        
        for pattern in status_patterns:
            if re.search(pattern, message_lower):
                return 'status'
        
        # Update/change requests
        update_patterns = [
            r'\b(update|modify|change|enhance|improve)\s+(code|function)',
            r'\bcode\s+(change|update|modification)',
            r'\bmake\s+(a\s+)?change\s+to\b',
            r'\bcan\s+you\s+(update|modify|change|fix)\b.*\bcode'
        ]
        
        for pattern in update_patterns:
            if re.search(pattern, message_lower):
                return 'update'
        
        # Self-update requests
        self_update_patterns = [
            r'\bupdate\s+(yourself|curie|the\s+system)\b',
            r'\bself[- ]?update\b',
            r'\bpull\s+(latest|new)\s+changes\b',
            r'\bcheck\s+for\s+updates\b'
        ]
        
        for pattern in self_update_patterns:
            if re.search(pattern, message_lower):
                return 'self_update'
        
        # Information about code changes
        info_patterns = [
            r'\bwhat\s+(code\s+)?changes\b',
            r'\btell\s+me\s+about\s+(the\s+)?(recent\s+)?changes\b',
            r'\bwhat\s+(did|have)\s+you\s+(change|update)',
            r'\bshow\s+me\s+(the\s+)?changes\b',
            r'\bcode\s+history\b',
            r'\bgit\s+log\b'
        ]
        
        for pattern in info_patterns:
            if re.search(pattern, message_lower):
                return 'info'
        
        # Pair programming requests
        pair_prog_patterns = [
            r'\b(start|begin)\s+(pair\s+)?programming\b',
            r'\bpair\s+program(ming)?\b',
            r'\bcode\s+together\b',
            r'\bcollaborate\s+on\s+code\b',
            r'\bworking\s+on\s+code\b',
            r'\bend\s+(pair\s+)?session\b'
        ]
        
        for pattern in pair_prog_patterns:
            if re.search(pattern, message_lower):
                return 'pair_programming'
        
        # Bug detection requests
        bug_detection_patterns = [
            r'\b(find|detect|check|scan)\s+(for\s+)?(bugs|issues|problems)\b',
            r'\bbug\s+(detection|finding|checking|scanning)\b',
            r'\banalyze\s+(for\s+)?(bugs|issues)\b',
            r'\bcheck\s+(for\s+)?vulnerabilities\b',
            r'\bsecurity\s+scan\b',
            r'\bproactive\s+(bug\s+)?finding\b'
        ]
        
        for pattern in bug_detection_patterns:
            if re.search(pattern, message_lower):
                return 'bug_detection'
        
        # Performance analysis requests
        performance_patterns = [
            r'\b(analyze|check|review)\s+(performance|speed|efficiency)\b',
            r'\bperformance\s+(analysis|review|check)\b',
            r'\boptimize\s+(my\s+)?code\b',
            r'\bcode\s+optimization\b',
            r'\bcheck\s+complexity\b',
            r'\bbig\s+o\b',
            r'\btime\s+complexity\b',
            r'\bmake.*faster\b',
            r'\bimprove\s+performance\b'
        ]
        
        for pattern in performance_patterns:
            if re.search(pattern, message_lower):
                return 'performance_analysis'
        
        # Code generation requests
        code_gen_patterns = [
            r'\bgenerate\s+code\b',
            r'\bcreate\s+(a\s+)?function\b',
            r'\bwrite\s+(a\s+)?(function|class|module)\b',
            r'\bcode\s+generation\b',
            r'\bscaffold\b',
            r'\bboilerplate\b',
            r'\btemplate\s+code\b'
        ]
        
        for pattern in code_gen_patterns:
            if re.search(pattern, message_lower):
                return 'code_generation'
        
        return None
    
    def get_service_status(self) -> str:
        """
        Get the status of the coding service
        
        Returns formatted status message
        """
        try:
            from services.coding_service import CodingService
            
            # Check if service is running
            service_enabled = os.getenv('RUN_CODING_SERVICE', 'false').lower() == 'true'
            
            if not service_enabled:
                return (
                    "🔧 **Coding Service Status**\n\n"
                    "The coding service is currently **disabled**.\n"
                    "To enable it, set `RUN_CODING_SERVICE=true` in your environment and restart.\n\n"
                    "**Available capabilities when enabled:**\n"
                    "- Code review on GitHub, GitLab, and Bitbucket\n"
                    "- Automated PR/MR creation\n"
                    "- Code analysis and suggestions\n"
                    "- Self-update functionality"
                )
            
            # Get platform availability
            platforms = []
            if os.getenv('GITHUB_TOKEN'):
                platforms.append("✓ GitHub")
            else:
                platforms.append("✗ GitHub (no token)")
            
            if os.getenv('GITLAB_TOKEN'):
                platforms.append("✓ GitLab")
            else:
                platforms.append("✗ GitLab (no token)")
            
            if os.getenv('BITBUCKET_USERNAME') and os.getenv('BITBUCKET_APP_PASSWORD'):
                platforms.append("✓ Bitbucket")
            else:
                platforms.append("✗ Bitbucket (no credentials)")
            
            platforms_str = "\n".join(f"- {p}" for p in platforms)
            
            return (
                f"🔧 **Coding Service Status**\n\n"
                f"Service: **Active** ✅\n\n"
                f"**Platform Support:**\n{platforms_str}\n\n"
                f"**Available Commands:**\n"
                f"- \"Review my code\" - Request code review\n"
                f"- \"Update the code\" - Make code changes\n"
                f"- \"Check for updates\" - Self-update system\n"
                f"- \"Show code changes\" - View recent changes"
            )
            
        except ImportError:
            return (
                "⚠️ Coding service module is not available. "
                "The required dependencies may not be installed."
            )
        except Exception as e:
            logger.error(f"Error getting coding service status: {e}", exc_info=True)
            return f"❌ Error getting service status: {str(e)}"
    
    def handle_review_request(self, message: str) -> str:
        """
        Handle code review requests
        
        Returns response message
        """
        # Extract context from message (file, PR number, etc.)
        message_lower = message.lower()
        
        # Check if user specified a file
        file_match = re.search(r'\bfile\s+([^\s]+)', message_lower)
        pr_match = re.search(r'\b(pr|pull request|mr|merge request)\s+#?(\d+)', message_lower)
        
        if file_match:
            file_name = file_match.group(1)
            return (
                f"📝 I can help review the file `{file_name}`.\n\n"
                f"To proceed, I'll need:\n"
                f"1. Repository path\n"
                f"2. Branch name\n\n"
                f"Alternatively, if you have a PR/MR open, just tell me the number!"
            )
        elif pr_match:
            pr_type = pr_match.group(1)
            pr_number = pr_match.group(2)
            return (
                f"🔍 I'll review {pr_type.upper()} #{pr_number}.\n\n"
                f"Which platform? (GitHub/GitLab/Bitbucket)\n\n"
                f"Once you confirm, I'll analyze the changes and provide feedback."
            )
        else:
            return (
                "🔍 **Code Review Service**\n\n"
                "I can help review your code! Please specify:\n\n"
                "**Option 1: Review a file**\n"
                "Example: \"Review file main.py\"\n\n"
                "**Option 2: Review a PR/MR**\n"
                "Example: \"Review PR #42 on GitHub\"\n\n"
                "**Option 3: Review changes**\n"
                "Example: \"Review changes in branch feature/new-feature\"\n\n"
                "What would you like me to review?"
            )
    
    def handle_update_request(self, message: str) -> str:
        """
        Handle code update/change requests
        
        Returns response message
        """
        return (
            "🔧 **Code Update Service**\n\n"
            "I can help make code changes! Please provide:\n\n"
            "1. **Goal**: What should the code do?\n"
            "   Example: \"Fix the login bug\" or \"Add error handling\"\n\n"
            "2. **Files**: Which files to modify?\n"
            "   Example: \"auth.py, utils.py\"\n\n"
            "3. **Branch**: Branch name for changes\n"
            "   Example: \"fix/login-bug\"\n\n"
            "I'll create a PR/MR with the changes and notify you when it's ready!"
        )
    
    def handle_self_update_request(self, message: str) -> str:
        """
        Handle self-update requests
        
        Returns response message
        """
        return (
            "🔄 **System Update**\n\n"
            "I can check for and apply updates to myself! This will:\n\n"
            "1. Check for new commits in the repository\n"
            "2. Pull the latest code\n"
            "3. Update dependencies if needed\n"
            "4. Create a backup before updating\n\n"
            "⚠️ **Note**: This requires proper Git and systemd configuration.\n\n"
            "Would you like me to:\n"
            "- **Check** for available updates (safe)\n"
            "- **Apply** updates (will restart service)\n\n"
            "Reply with 'check' or 'apply' to proceed."
        )
    
    def handle_info_request(self, message: str) -> str:
        """
        Handle requests for information about code changes
        
        Returns response message
        """
        try:
            from agent.skills.github_integration import GitHubIntegration
            
            # Check if we can access git
            try:
                gh = GitHubIntegration()
                # Get recent commits
                commits = gh.git_log(max_count=5)
                
                commit_list = "\n".join([
                    f"- `{c['sha']}` {c['message']} by {c['author']}"
                    for c in commits
                ])
                
                return (
                    f"📊 **Recent Code Changes**\n\n"
                    f"**Last 5 commits:**\n{commit_list}\n\n"
                    f"You can also ask me to:\n"
                    f"- Show git status\n"
                    f"- Show git diff\n"
                    f"- List branches"
                )
            except Exception as e:
                logger.debug(f"Could not get git info: {e}")
                return (
                    "📊 **Recent Code Changes**\n\n"
                    "I can show you information about:\n\n"
                    "- Recent commits in the repository\n"
                    "- Open PRs/MRs across platforms\n"
                    "- Code changes made by the coding service\n"
                    "- Files modified recently\n\n"
                    "What specific information would you like to see?"
                )
            
        except ImportError:
            return (
                "⚠️ Code change tracking is not available. "
                "The coding service module may not be installed."
            )
    
    def handle_git_operation(self, message: str) -> str:
        """
        Handle git operation requests
        
        Returns response message
        """
        try:
            from agent.skills.github_integration import GitHubIntegration
            gh = GitHubIntegration()
            
            message_lower = message.lower()
            
            # Git status
            if 'status' in message_lower:
                status = gh.git_status()
                staged = len(status['staged'])
                unstaged = len(status['unstaged'])
                untracked = len(status['untracked'])
                
                return (
                    f"📋 **Git Status**\n\n"
                    f"- **Staged:** {staged} files\n"
                    f"- **Unstaged:** {unstaged} files\n"
                    f"- **Untracked:** {untracked} files\n\n"
                    f"What would you like to do next?\n"
                    f"- Stage files: \"add all files\" or \"add file.py\"\n"
                    f"- Commit: \"commit with message: your message\"\n"
                    f"- Push: \"push to remote\""
                )
            
            # Git diff
            elif 'diff' in message_lower:
                cached = 'cached' in message_lower or 'staged' in message_lower
                diff = gh.git_diff(cached=cached)
                
                if not diff:
                    return "No changes to show."
                
                # Truncate if too long
                if len(diff) > 1000:
                    diff = diff[:1000] + "\n... (truncated)"
                
                return f"```diff\n{diff}\n```"
            
            # List branches
            elif 'branch' in message_lower and 'list' in message_lower:
                branches = gh.git_branch()
                branch_list = "\n".join([f"- {b}" for b in branches])
                return f"**Branches:**\n{branch_list}"
            
            # General git operations guide
            else:
                return (
                    "🔧 **Git Operations**\n\n"
                    "I can help you with:\n\n"
                    "**Status & Info:**\n"
                    "- \"Show git status\" - Check repo status\n"
                    "- \"Show git diff\" - View unstaged changes\n"
                    "- \"Show staged diff\" - View staged changes\n"
                    "- \"List branches\" - Show all branches\n"
                    "- \"Show git log\" - Recent commits\n\n"
                    "**Making Changes:**\n"
                    "- \"Add all files\" - Stage all changes\n"
                    "- \"Add file.py\" - Stage specific file\n"
                    "- \"Commit with message: description\" - Commit staged changes\n"
                    "- \"Push to remote\" - Push commits\n"
                    "- \"Pull from remote\" - Pull latest changes\n\n"
                    "**Branches:**\n"
                    "- \"Create branch name\" - Create new branch\n"
                    "- \"Checkout branch name\" - Switch branches\n\n"
                    "What would you like to do?"
                )
                
        except Exception as e:
            logger.error(f"Git operation failed: {e}", exc_info=True)
            return f"❌ Git operation failed: {str(e)}"
    
    def handle_file_edit(self, message: str) -> str:
        """
        Handle file editing requests
        
        Returns response message
        """
        try:
            from agent.skills.github_integration import GitHubIntegration
            
            return (
                "📝 **File Editing**\n\n"
                "I can help you edit files! Please provide:\n\n"
                "**To edit a file:**\n"
                "- File name: `auth.py`\n"
                "- What to change: \"Replace old function with new one\"\n\n"
                "**To create a file:**\n"
                "Example: \"Create file new_module.py with content...\"\n\n"
                "**To read a file:**\n"
                "Example: \"Show me the contents of config.py\"\n\n"
                "**To delete a file:**\n"
                "Example: \"Delete temp_file.py\"\n\n"
                "What would you like to do?"
            )
            
        except ImportError:
            return (
                "⚠️ File editing is not available. "
                "The GitHub integration module may not be installed."
            )
    
    def handle_pair_programming(self, message: str) -> str:
        """
        Handle pair programming requests
        
        Returns response message
        """
        try:
            from agent.skills.pair_programming import get_pair_programming
            
            pp = get_pair_programming()
            message_lower = message.lower()
            
            # FIXME: User ID should come from the chat session context
            # Currently hardcoded for demonstration - this means all users share the same session
            # In production, extract from normalized_input or session context in chat_workflow
            user_id = "default_user"  # TODO: Get from session context - see chat_workflow.py process_message()
            
            # Start session
            if any(word in message_lower for word in ['start', 'begin']):
                # Only treat text after explicit "on"/"for" as the task, e.g.:
                # "start pair programming on refactoring the API"
                task_match = re.search(r'(?:start|begin).*?(?:on|for)\s+(.+)', message_lower)
                task = task_match.group(1) if task_match else None
                return pp.start_session(user_id, task)
            
            # End session
            elif 'end' in message_lower:
                return pp.end_session(user_id)
            
            # Status
            elif 'status' in message_lower:
                return pp.get_session_status(user_id)
            
            # Add file
            elif 'add file' in message_lower:
                file_match = re.search(r'add file\s+(.+)', message_lower)
                if file_match:
                    filepath = file_match.group(1).strip()
                    return pp.add_file_to_session(user_id, filepath)
            
            # Default response
            return (
                "🤝 **Pair Programming**\n\n"
                "I can help you code collaboratively! Commands:\n\n"
                "- **Start session:** \"Start pair programming [task]\"\n"
                "- **End session:** \"End pair programming\"\n"
                "- **Add file:** \"Add file path/to/file.py\"\n"
                "- **Get status:** \"Pair programming status\"\n\n"
                "Let's code together!"
            )
            
        except ImportError as e:
            logger.error(f"Pair programming module not available: {e}")
            return (
                "⚠️ Pair programming is not available. "
                "The pair programming module may not be installed."
            )
    
    def handle_bug_detection(self, message: str) -> str:
        """
        Handle bug detection requests
        
        Returns response message
        """
        try:
            from agent.skills.bug_detector import get_bug_detector
            
            detector = get_bug_detector()
            message_lower = message.lower()
            
            # Extract file path if provided
            file_match = re.search(r'(?:file|in)\s+([^\s]+\.(?:py|js|ts|java|go|rs|rb|php))', message_lower)
            
            if file_match:
                filepath = file_match.group(1)
                try:
                    results = detector.detect_bugs_in_file(filepath)
                    return detector.format_findings_report(results)
                except Exception as e:
                    return f"❌ Error analyzing file: {str(e)}"
            
            # Proactive scan
            elif 'scan' in message_lower or 'proactive' in message_lower:
                dir_match = re.search(r'(?:scan|in)\s+directory\s+([^\s]+)', message_lower)
                directory = dir_match.group(1) if dir_match else '.'
                
                return (
                    f"🔍 **Proactive Bug Scanning**\n\n"
                    f"Starting scan of `{directory}`...\n"
                    f"This will analyze all code files for potential bugs.\n\n"
                    f"Note: For best results, specify a file path:\n"
                    f"\"Find bugs in file path/to/file.py\""
                )
            
            # General help
            else:
                return (
                    "🐛 **Bug Detection**\n\n"
                    "I can help find bugs in your code! Options:\n\n"
                    "**Analyze a file:**\n"
                    "Example: \"Find bugs in file auth.py\"\n\n"
                    "**Proactive scanning:**\n"
                    "Example: \"Scan for bugs in directory src/\"\n\n"
                    "**What I check for:**\n"
                    "- Security vulnerabilities\n"
                    "- Common anti-patterns\n"
                    "- Potential bugs\n"
                    "- Code quality issues\n\n"
                    "What would you like me to check?"
                )
                
        except ImportError as e:
            logger.error(f"Bug detector module not available: {e}")
            return (
                "⚠️ Bug detection is not available. "
                "The bug detector module may not be installed."
            )
    
    def handle_performance_analysis(self, message: str) -> str:
        """
        Handle performance analysis requests
        
        Returns response message
        """
        try:
            from agent.skills.performance_analyzer import get_performance_analyzer
            
            analyzer = get_performance_analyzer()
            message_lower = message.lower()
            
            # Extract file path if provided
            file_match = re.search(r'(?:file|in)\s+([^\s]+\.(?:py|js|ts|java|go|rs|rb|php))', message_lower)
            
            if file_match:
                filepath = file_match.group(1)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        code = f.read()
                    
                    # Detect language
                    ext = filepath.split('.')[-1]
                    lang_map = {'py': 'python', 'js': 'javascript', 'ts': 'typescript'}
                    language = lang_map.get(ext)
                    
                    report = analyzer.generate_optimization_report(code, language, filepath)
                    return report
                except Exception as e:
                    return f"❌ Error analyzing file: {str(e)}"
            
            # General help
            else:
                return (
                    "⚡ **Performance Analysis**\n\n"
                    "I can analyze your code for performance issues!\n\n"
                    "**Analyze a file:**\n"
                    "Example: \"Analyze performance of file utils.py\"\n\n"
                    "**What I analyze:**\n"
                    "- Time complexity (Big O)\n"
                    "- Space complexity\n"
                    "- Performance bottlenecks\n"
                    "- Optimization suggestions\n"
                    "- Code complexity metrics\n\n"
                    "**Optimization tips:**\n"
                    "- Algorithm improvements\n"
                    "- Data structure suggestions\n"
                    "- Memory optimizations\n"
                    "- Caching opportunities\n\n"
                    "Which file would you like me to analyze?"
                )
                
        except ImportError as e:
            logger.error(f"Performance analyzer module not available: {e}")
            return (
                "⚠️ Performance analysis is not available. "
                "The performance analyzer module may not be installed."
            )
    
    def handle_code_generation(self, message: str) -> str:
        """
        Handle code generation requests
        
        Returns response message
        """
        try:
            # Extract what to generate
            message_lower = message.lower()
            
            # Detect type of code to generate
            if 'function' in message_lower:
                code_type = 'function'
            elif 'class' in message_lower:
                code_type = 'class'
            elif 'module' in message_lower:
                code_type = 'module'
            elif 'api' in message_lower:
                code_type = 'API endpoint'
            else:
                code_type = 'code'
            
            # Extract language if specified
            language = None
            for lang in ['python', 'javascript', 'typescript', 'java', 'go', 'rust']:
                if lang in message_lower:
                    language = lang
                    break
            
            lang_str = f" in {language.title()}" if language else ""
            
            return (
                f"💻 **Code Generation**\n\n"
                f"I can help generate {code_type}{lang_str}!\n\n"
                f"**To get started, please provide:**\n\n"
                f"1. **Purpose:** What should the {code_type} do?\n"
                f"   Example: \"Calculate Fibonacci numbers\"\n\n"
                f"2. **Language:** {language.title() if language else 'Which language?'}\n\n"
                f"3. **Specifications:**\n"
                f"   - Input parameters\n"
                f"   - Return type\n"
                f"   - Special requirements\n\n"
                f"**Example request:**\n"
                f"\"Generate a Python function that validates email addresses\n"
                f"with regex, takes a string input, and returns a boolean.\"\n\n"
                f"What would you like me to generate?"
            )
            
        except Exception as e:
            logger.error(f"Code generation failed: {e}")
            return f"❌ Code generation failed: {str(e)}"
    
    async def handle_message(self, message: str) -> Optional[str]:
        """
        Main entry point for handling coding-related messages
        
        Args:
            message: User's message
            
        Returns:
            Response string if message was handled, None otherwise
        """
        intent = self.detect_coding_intent(message)
        
        if intent is None:
            return None
        
        logger.info(f"Detected coding intent: {intent}")
        
        if intent == 'status':
            return self.get_service_status()
        elif intent == 'review':
            return self.handle_review_request(message)
        elif intent == 'update':
            return self.handle_update_request(message)
        elif intent == 'self_update':
            return self.handle_self_update_request(message)
        elif intent == 'info':
            return self.handle_info_request(message)
        elif intent == 'git_op':
            return self.handle_git_operation(message)
        elif intent == 'edit_file':
            return self.handle_file_edit(message)
        elif intent == 'pair_programming':
            return self.handle_pair_programming(message)
        elif intent == 'bug_detection':
            return self.handle_bug_detection(message)
        elif intent == 'performance_analysis':
            return self.handle_performance_analysis(message)
        elif intent == 'code_generation':
            return self.handle_code_generation(message)
        
        return None


# Global instance for easy access
_coding_assistant = None


def get_coding_assistant() -> CodingAssistant:
    """Get or create the global coding assistant instance"""
    global _coding_assistant
    if _coding_assistant is None:
        _coding_assistant = CodingAssistant()
    return _coding_assistant


async def handle_coding_query(message: str) -> Optional[str]:
    """
    Convenience function to handle coding queries
    
    Args:
        message: User's message
        
    Returns:
        Response string if query was handled, None otherwise
    """
    assistant = get_coding_assistant()
    return await assistant.handle_message(message)
