# main.py

import argparse
import os
import threading
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
import faulthandler
import signal
import time
import re


class _SecretRedactionFilter(logging.Filter):
    _pattern = re.compile(
        r"(?i)(bearer\s+\S+|(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+)"
    )

    def filter(self, record):
        rendered = record.getMessage()
        record.msg = self._pattern.sub("[REDACTED]", rendered)
        record.args = ()
        return True


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _restrict(self):
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass

    def doRollover(self):
        super().doRollover()
        self._restrict()
        for index in range(1, self.backupCount + 1):
            try:
                os.chmod(f"{self.baseFilename}.{index}", 0o600)
            except OSError:
                pass


# Check for critical dependencies early to provide helpful error messages
try:
    from connectors.telegram import (
        start_telegram_bot,
        send_message as send_telegram_message,
        set_workflow as set_telegram_workflow,
        is_ready as telegram_is_ready,
    )
    from connectors.lifecycle import ConnectorApplication, ConnectorRegistry
    from connectors.api import app as fastapi_app, set_workflow as set_api_workflow
    from memory import init_memory
    from llm import manager
    import uvicorn

    from agent.chat_workflow import ChatWorkflow
    from utils.persona import load_persona, list_available_personas
    import asyncio
except ModuleNotFoundError as e:
    print(f"\n{'='*70}")
    print("ERROR: Missing required Python dependency")
    print(f"{'='*70}\n")
    print(f"Module not found: {e.name}")
    print("\nThis error occurs when required dependencies are not installed.")
    print("\nTo fix this issue, please install all dependencies:\n")
    print("  pip install -r requirements.txt\n")
    print("After installation, verify your setup:\n")
    print("  python scripts/verify_setup.py\n")
    print(f"{'='*70}\n")
    print("For more help, see:")
    print("  - docs/QUICK_START.md")
    print("  - docs/TROUBLESHOOTING.md")
    print(f"{'='*70}\n")
    sys.exit(1)

logger = logging.getLogger(__name__)
DEFAULT_MAIN_REPO_URL = "https://github.com/yessur3808/curie-ai"

# Import Discord connector (optional - may not be installed)
try:
    from connectors.discord_bot import (
        start_discord_bot,
        send_message as send_discord_message,
        set_workflow as set_discord_workflow,
    )

    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    logger.warning("Discord connector not available (discord.py not installed)")

# Import WhatsApp connector (optional - may not be installed)
try:
    from connectors.whatsapp import (
        start_whatsapp_bot,
        set_workflow as set_whatsapp_workflow,
    )

    WHATSAPP_AVAILABLE = True
except ImportError:
    WHATSAPP_AVAILABLE = False
    logger.warning("WhatsApp connector not available (whatsapp-web.py not installed)")

# Import Slack connector (optional - requires slack-bolt)
try:
    from connectors.slack import (
        start_slack_bot,
        set_workflow as set_slack_workflow,
    )

    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False
    logger.warning("Slack connector not available (slack-bolt not installed)")

# Import Signal connector (optional - requires signal-cli REST API)
try:
    from connectors.signal import (
        start_signal_bot,
        set_workflow as set_signal_workflow,
    )

    SIGNAL_AVAILABLE = True
except ImportError:
    SIGNAL_AVAILABLE = False
    logger.warning("Signal connector not available")

# Import Microsoft Teams connector (mounts on FastAPI app)
try:
    from connectors.teams import (
        mount_on as mount_teams,
        set_workflow as set_teams_workflow,
    )

    TEAMS_AVAILABLE = True
    mount_teams(fastapi_app)
except ImportError:
    TEAMS_AVAILABLE = False
    logger.warning("Microsoft Teams connector not available")

# Import LINE connector (mounts on FastAPI app)
try:
    from connectors.line import (
        mount_on as mount_line,
        set_workflow as set_line_workflow,
    )

    LINE_AVAILABLE = True
    mount_line(fastapi_app)
except ImportError:
    LINE_AVAILABLE = False
    logger.warning("LINE connector not available")

# Import KakaoTalk connector (mounts on FastAPI app)
try:
    from connectors.kakaotalk import (
        mount_on as mount_kakao,
        set_workflow as set_kakao_workflow,
    )

    KAKAO_AVAILABLE = True
    mount_kakao(fastapi_app)
except ImportError:
    KAKAO_AVAILABLE = False
    logger.warning("KakaoTalk connector not available")


def configure_logging():
    """
    Configure logging for the application at startup.

    Sets up logging to capture all log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    with a consistent format across all modules. This ensures that important logs,
    including security-related messages from URL validation, are properly captured.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv(
        "LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Configure root logger
    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = os.getenv("CURIE_LOG_FILE")
    if log_file:
        log_path = os.path.abspath(log_file)
        os.makedirs(os.path.dirname(log_path), mode=0o700, exist_ok=True)
        file_handler = _PrivateRotatingFileHandler(
            log_path,
            maxBytes=max(64_000, int(os.getenv("CURIE_LOG_MAX_BYTES", "5000000"))),
            backupCount=max(1, int(os.getenv("CURIE_LOG_BACKUP_COUNT", "5"))),
            encoding="utf-8",
        )
        file_handler._restrict()
        handlers.append(file_handler)
    for handler in handlers:
        handler.addFilter(_SecretRedactionFilter())
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )

    # SIGUSR1 produces a stack dump without terminating Curie. This makes a
    # process that is alive but stuck during startup diagnosable under PM2.
    try:
        faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    except (AttributeError, OSError, RuntimeError):
        logger.debug("Runtime stack-dump signal is unavailable", exc_info=True)

    # httpx logs complete request URLs at INFO. Telegram embeds the bot token
    # in that URL, so request-level transport logs must never reach daemon logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Use the module logger here. Keeping a function-local assignment made the
    # earlier exception handler reference an uninitialized local variable when
    # faulthandler registration was unavailable.
    logger.info(f"Logging configured with level: {log_level}")


def load_all_workflows():
    """Create one active conversation workflow per available personality."""
    workflows = {}
    for persona_info in list_available_personas():
        persona = load_persona(persona_info["filename"])
        name = persona["name"]
        workflows[name] = ChatWorkflow(persona=persona)
    return workflows


def load_default_workflow(persona_filename=None):
    """Create the same workflow implementation used by every connector."""
    persona = load_persona(filename=persona_filename)
    return ChatWorkflow(persona=persona)


# Directories that are never interesting for code editing.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".env",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "node_modules",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
    "site-packages",
}

_AUTO_DETECT_WARN_THRESHOLD = 50  # warn before processing a very large file list


# --- Helper: Find all files recursively ---
def find_all_files(repo_path, exts=None):
    all_files = []
    for root, dirs, files in os.walk(repo_path):
        # Prune directories in-place so os.walk won't descend into them.
        dirs[:] = [
            d for d in dirs if d not in _SKIP_DIRS and not d.endswith(".egg-info")
        ]
        for f in files:
            rel_path = os.path.relpath(os.path.join(root, f), repo_path)
            if exts is None or any(rel_path.endswith(ext) for ext in exts):
                all_files.append(rel_path)
    return all_files


def suggest_branch_name(goal):
    sanitized_goal = re.sub(r"[^a-z0-9]+", "-", (goal or "").lower()).strip("-")
    if not sanitized_goal:
        sanitized_goal = "update"
    branch_slug = sanitized_goal[:48].strip("-") or "update"
    return f"enhancement/{branch_slug}"


def resolve_branch_name(goal, branch_name):
    cleaned_branch = (branch_name or "").strip()
    if cleaned_branch:
        return cleaned_branch
    return suggest_branch_name(goal)


# --- Connector Runners ---


def run_telegram(workflow: ChatWorkflow):
    print("Starting Telegram connector...")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    start_telegram_bot(workflow)


def run_discord(workflow: ChatWorkflow):
    """Run Discord connector."""
    if not DISCORD_AVAILABLE:
        logger.error("Discord connector is not available. Install discord.py first.")
        return

    print("Starting Discord connector...")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    start_discord_bot(workflow)


def run_whatsapp(workflow: ChatWorkflow):
    """Run WhatsApp connector."""
    if not WHATSAPP_AVAILABLE:
        logger.error(
            "WhatsApp connector is not available. Install whatsapp-web.py first."
        )
        return

    print("Starting WhatsApp connector...")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    start_whatsapp_bot(workflow)


def run_slack(workflow: ChatWorkflow):
    """Run Slack connector."""
    if not SLACK_AVAILABLE:
        logger.error(
            "Slack connector is not available. Install slack-bolt first: "
            "pip install slack-bolt\n"
            "Also set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in your .env file."
        )
        return

    print("Starting Slack connector (Socket Mode)...")
    start_slack_bot(workflow)


def run_signal(workflow: ChatWorkflow):
    """Run Signal connector polling loop."""
    if not SIGNAL_AVAILABLE:
        logger.error("Signal connector is not available.")
        return

    print("Starting Signal connector...")
    start_signal_bot(workflow)


def run_api():
    print("Starting API (FastAPI) connector on http://0.0.0.0:8000 ...")
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_level="info")


def run_coding_service(workflow: ChatWorkflow):
    """Run the standalone coding service in parallel with other modules"""
    logger.info("Starting Coding Service...")

    try:
        from services.coding_service import CodingService

        # Create notification callback to send messages to master user
        def notify_master_user(message: str, data: dict):
            master_user_id = os.getenv("MASTER_USER_ID")
            if master_user_id:
                # Store notification in memory for master user to retrieve
                logger.info(f"Coding Service notification for master: {message}")
                # You could extend this to send actual messages via connectors

        service = CodingService(notification_callback=notify_master_user)
        service.start()
        logger.info("✅ Coding service initialized and started successfully")

        # Keep service running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping coding service...")
            service.stop()

    except Exception as e:
        logger.error(f"❌ Failed to start coding service: {e}", exc_info=True)
        raise


def run_coder_interactive():
    print("Starting Coder skill (interactive mode)...")
    from agent.skills.coder import apply_code_change

    if not os.getenv("MAIN_REPO"):
        os.environ["MAIN_REPO"] = DEFAULT_MAIN_REPO_URL
        print(f"MAIN_REPO not set. Using default: {DEFAULT_MAIN_REPO_URL}")

    goal = input("Describe the code enhancement goal: ").strip()
    if not goal:
        print("Error: goal is required.")
        return

    repo_raw = input("Enter local repo path (absolute or relative) [.]: ").strip()
    repo_path = repo_raw or "."

    raw_branch_name = input(
        "Enter the branch name to use (press Enter to auto-generate): "
    ).strip()
    branch_name = resolve_branch_name(goal, raw_branch_name)
    if not raw_branch_name:
        print(f"Auto-generated branch name: {branch_name}")

    files_raw = input(
        "Comma-separated filenames to edit (press Enter to auto-detect .py files): "
    ).strip()
    if files_raw:
        files_to_edit = [f.strip() for f in files_raw.split(",") if f.strip()]
    else:
        files_to_edit = find_all_files(repo_path, exts=[".py"])
        print(f"Auto-detected {len(files_to_edit)} Python file(s) in {repo_path}.")
        if len(files_to_edit) > _AUTO_DETECT_WARN_THRESHOLD:
            confirm = (
                input(
                    f"⚠️  That's a large number of files ({len(files_to_edit)}). "
                    "Proceed? [y/N]: "
                )
                .strip()
                .lower()
            )
            if confirm != "y":
                print("Aborted. Please specify files manually.")
                return
    print(f"Running code enhancement for files: {files_to_edit} ...")
    result = apply_code_change(goal, files_to_edit, repo_path, branch_name)
    print("\n---\nResult:\n")
    print("Branch:", result[0])
    print("Files changed:", list(result[1].keys()))
    print("PR URL:", result[2])


def run_coder_batch(goal, files_to_edit, repo_path, branch_name):
    print("Starting Coder skill (batch mode)...")
    from agent.skills.coder import apply_code_change

    if not os.getenv("MAIN_REPO"):
        os.environ["MAIN_REPO"] = DEFAULT_MAIN_REPO_URL
        print(f"MAIN_REPO not set. Using default: {DEFAULT_MAIN_REPO_URL}")

    branch_name = resolve_branch_name(goal, branch_name)

    print(f"Goal: {goal}")
    print(f"Repo path: {repo_path}")
    print(f"Branch: {branch_name}")
    print(f"Files to edit: {files_to_edit}")
    result = apply_code_change(goal, files_to_edit, repo_path, branch_name)
    print("\n---\nResult:\n")
    print("Branch:", result[0])
    print("Files changed:", list(result[1].keys()))
    print("PR URL:", result[2])


# --- Argument Parsing ---
def parse_args():
    parser = argparse.ArgumentParser(description="Start Curie AI Connectors")
    parser.add_argument(
        "--telegram", action="store_true", help="Run Telegram connector"
    )
    parser.add_argument("--discord", action="store_true", help="Run Discord connector")
    parser.add_argument(
        "--whatsapp", action="store_true", help="Run WhatsApp connector"
    )
    parser.add_argument(
        "--api", action="store_true", help="Run API connector (FastAPI)"
    )
    parser.add_argument(
        "--slack", action="store_true", help="Run Slack connector (Socket Mode)"
    )
    parser.add_argument("--signal", action="store_true", help="Run Signal connector")
    parser.add_argument(
        "--coding-service", action="store_true", help="Run standalone coding service"
    )
    parser.add_argument(
        "--coder", action="store_true", help="Run coder/PR skill (interactive)"
    )
    parser.add_argument(
        "--coder-batch",
        action="store_true",
        help="Run coder in batch mode (non-interactive)",
    )
    parser.add_argument(
        "--coder-config", type=str, help="JSON file with coder batch parameters"
    )
    parser.add_argument("--coder-goal", type=str, help="Goal for coder batch mode")
    parser.add_argument(
        "--coder-files", type=str, help="Comma-separated file list for coder batch mode"
    )
    parser.add_argument("--coder-repo", type=str, help="Repo path for coder batch mode")
    parser.add_argument(
        "--coder-branch", type=str, help="Branch name for coder batch mode"
    )
    parser.add_argument("--all", action="store_true", help="Run all connectors")
    parser.add_argument(
        "--no-init", action="store_true", help="Skip model preload and memory init"
    )
    parser.add_argument(
        "--persona", type=str, help="Filename of persona to use (in assets/personality)"
    )
    return parser.parse_args()


# --- Config Determination ---
def determine_what_to_run(args):
    run_telegram_env = os.getenv("RUN_TELEGRAM", "false").lower() == "true"
    run_discord_env = os.getenv("RUN_DISCORD", "false").lower() == "true"
    run_whatsapp_env = os.getenv("RUN_WHATSAPP", "false").lower() == "true"
    run_api_env = os.getenv("RUN_API", "false").lower() == "true"
    run_coder_env = os.getenv("RUN_CODER", "false").lower() == "true"
    run_coding_service_env = os.getenv("RUN_CODING_SERVICE", "false").lower() == "true"
    run_slack_env = os.getenv("RUN_SLACK", "false").lower() == "true"
    run_signal_env = os.getenv("RUN_SIGNAL", "false").lower() == "true"

    run_telegram_flag = args.all or args.telegram or run_telegram_env
    run_discord_flag = args.all or args.discord or run_discord_env
    run_whatsapp_flag = args.all or args.whatsapp or run_whatsapp_env
    run_api_flag = args.all or args.api or run_api_env
    run_coder_flag = args.all or args.coder or run_coder_env
    run_coder_batch_flag = args.coder_batch
    run_coding_service_flag = args.coding_service or run_coding_service_env
    run_slack_flag = args.all or args.slack or run_slack_env
    run_signal_flag = args.all or args.signal or run_signal_env

    if not (
        run_telegram_flag
        or run_discord_flag
        or run_whatsapp_flag
        or run_api_flag
        or run_coder_flag
        or run_coder_batch_flag
        or run_coding_service_flag
        or run_slack_flag
        or run_signal_flag
    ):
        print(
            "Nothing to run! Use --telegram, --discord, --whatsapp, --api, "
            "--slack, --signal, --coder, --coder-batch, --coding-service, --all "
            "or set RUN_* in .env."
        )
        sys.exit(1)
    return (
        run_telegram_flag,
        run_discord_flag,
        run_whatsapp_flag,
        run_api_flag,
        run_coder_flag,
        run_coder_batch_flag,
        run_coding_service_flag,
        run_slack_flag,
        run_signal_flag,
    )


def init_llm_and_memory(no_init):
    if no_init:
        return

    # Initialise the database synchronously — connectors need it before they
    # can handle any message.
    print("Initializing memory...")
    init_memory()
    try:
        from services.security import enforce_retention

        removed = enforce_retention()
        if any(removed.values()):
            logger.info("Applied startup retention policy: %s", removed)
    except Exception as exc:
        # Retention failures must be visible without preventing the owner from
        # reaching Curie to inspect or correct the storage configuration.
        logger.warning("Could not apply startup retention policy: %s", exc)

    # Start the LLM model loading in a background daemon thread so connectors
    # (Telegram, Discord, API, …) can come online immediately.  The first
    # ask_llm() call that needs the local model will wait for the preload to
    # finish, but users can connect and send messages right away.
    print(
        "LLM model loading started in background — connectors will start immediately."
    )
    manager.start_background_preload()


# --- Coder Batch Mode Helpers ---
def get_batch_coder_params_from_config(config_path):
    if not os.path.exists(config_path):
        print(f"Error: coder config file {config_path} not found.")
        sys.exit(1)
    with open(config_path) as f:
        config = json.load(f)
    goal = config.get("goal")
    files_arg = config.get("files_to_edit")
    repo_path = config.get("repo_path")
    branch_name = config.get("branch_name")
    if isinstance(files_arg, str) and files_arg.strip().lower().startswith("all"):
        exts = None
        if ":" in files_arg:
            ext_part = files_arg.split(":", 1)[1]
            exts = [
                f".{e.strip()}" if not e.startswith(".") else e.strip()
                for e in ext_part.split(",")
                if e.strip()
            ]
        if not repo_path:
            print("Error: Must supply repo_path with files_to_edit=all or all:ext")
            sys.exit(1)
        files_to_edit = find_all_files(repo_path, exts)
        print(f"Discovered {len(files_to_edit)} files to edit in {repo_path}.")
    else:
        files_to_edit = (
            [f.strip() for f in (files_arg or [])]
            if isinstance(files_arg, list)
            else [f.strip() for f in (files_arg or "").split(",") if f.strip()]
        )
    return goal, files_to_edit, repo_path, branch_name


def get_batch_coder_params_from_cli(args):
    files_arg = args.coder_files
    repo_path = args.coder_repo
    if files_arg and files_arg.strip().lower().startswith("all"):
        exts = None
        if ":" in files_arg:
            ext_part = files_arg.split(":", 1)[1]
            exts = [
                f".{e.strip()}" if not e.startswith(".") else e.strip()
                for e in ext_part.split(",")
                if e.strip()
            ]
        if not repo_path:
            print("Error: Must supply --coder-repo with --coder-files=all or all:ext")
            sys.exit(1)
        files_to_edit = find_all_files(repo_path, exts)
        print(f"Discovered {len(files_to_edit)} files to edit in {repo_path}.")
    else:
        files_to_edit = [f.strip() for f in (files_arg or "").split(",") if f.strip()]
    goal = args.coder_goal
    branch_name = args.coder_branch
    return goal, files_to_edit, repo_path, branch_name


def validate_coder_batch_params(goal, files_to_edit, repo_path, branch_name):
    missing = []
    if not goal:
        missing.append("goal")
    if not files_to_edit:
        missing.append("files_to_edit")
    if not repo_path:
        missing.append("repo_path")
    if missing:
        print(f"Error: Missing batch coder parameters: {', '.join(missing)}")
        sys.exit(1)


# --- Main Orchestration ---
def main():
    # Configure logging first thing to capture all logs
    configure_logging()

    args = parse_args()

    # Default to REST API when no connector has been explicitly requested so
    # that a bare ``python main.py`` (or an unconfigured .env) still works.
    _anything_arg = any(
        [
            args.all,
            args.telegram,
            args.discord,
            args.whatsapp,
            args.api,
            args.coder,
            args.coder_batch,
            args.coding_service,
            args.slack,
            args.signal,
        ]
    )
    _anything_env = any(
        os.getenv(v, "false").lower() == "true"
        for v in (
            "RUN_TELEGRAM",
            "RUN_DISCORD",
            "RUN_WHATSAPP",
            "RUN_API",
            "RUN_CODER",
            "RUN_CODING_SERVICE",
            "RUN_SLACK",
            "RUN_SIGNAL",
        )
    )
    if not _anything_arg and not _anything_env:
        logger.info(
            "No connector specified — defaulting to REST API (http://0.0.0.0:8000).\n"
            "  Tip: pass --telegram, --discord, --all, etc. or set RUN_* in .env.\n"
            "  Example: python main.py --api --telegram"
        )
        args.api = True

    (
        run_telegram_flag,
        run_discord_flag,
        run_whatsapp_flag,
        run_api_flag,
        run_coder_flag,
        run_coder_batch_flag,
        run_coding_service_flag,
        run_slack_flag,
        run_signal_flag,
    ) = determine_what_to_run(args)
    init_llm_and_memory(args.no_init)

    # Load persona and initialize ChatWorkflow
    persona_arg = getattr(args, "persona", None)
    persona = load_persona(filename=persona_arg)
    # Use minimal_sanitization from env or default to True for natural chat
    minimal_sanitization = os.getenv("MINIMAL_SANITIZATION", "true").lower() == "true"
    workflow = ChatWorkflow(
        persona=persona,
        max_history=max(5, int(os.getenv("CHAT_MAX_HISTORY", "25"))),
        enable_small_talk=False,
        minimal_sanitization=minimal_sanitization,
    )
    logger.info(
        f"✅ ChatWorkflow initialized with persona: {workflow.persona.get('name')}"
    )

    # Share workflow with connectors
    if (
        run_telegram_flag
        or run_discord_flag
        or run_whatsapp_flag
        or run_api_flag
        or run_coding_service_flag
        or run_slack_flag
        or run_signal_flag
    ):
        set_telegram_workflow(workflow)
        set_api_workflow(workflow)
        if DISCORD_AVAILABLE:
            set_discord_workflow(workflow)
        if WHATSAPP_AVAILABLE:
            set_whatsapp_workflow(workflow)
        if SLACK_AVAILABLE:
            set_slack_workflow(workflow)
        if SIGNAL_AVAILABLE:
            set_signal_workflow(workflow)
        if TEAMS_AVAILABLE:
            set_teams_workflow(workflow)
        if LINE_AVAILABLE:
            set_line_workflow(workflow)
        if KAKAO_AVAILABLE:
            set_kakao_workflow(workflow)

    threads = []
    connector_registry = ConnectorRegistry()

    # Initialize proactive messaging after outbound connectors start below.
    proactive_service = None
    x_autopost_service = None
    enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"
    if not enable_proactive:
        logger.info(
            "ℹ️  Proactive messaging is disabled via ENABLE_PROACTIVE_MESSAGING env variable"
        )

    # Telegram runs in a thread so proactive delivery and other connectors can
    # coexist in the same process.
    if run_telegram_flag:
        telegram_connector = ConnectorApplication(
            "telegram",
            run_telegram,
            send_fn=send_telegram_message,
            ready_probe=telegram_is_ready,
            queue_capacity=int(os.getenv("CONNECTOR_OUTBOUND_QUEUE_SIZE", "64")),
        )
        connector_registry.register(telegram_connector)
        threads.append(telegram_connector.start(workflow))

    # Start Discord bot in thread
    if run_discord_flag:
        if DISCORD_AVAILABLE:
            connector = ConnectorApplication(
                "discord",
                run_discord,
                send_fn=send_discord_message,
                queue_capacity=int(os.getenv("CONNECTOR_OUTBOUND_QUEUE_SIZE", "64")),
            )
            connector_registry.register(connector)
            threads.append(connector.start(workflow))
        else:
            logger.error("Discord connector requested but not available")

    # Start WhatsApp bot in thread
    if run_whatsapp_flag:
        if WHATSAPP_AVAILABLE:
            connector = ConnectorApplication("whatsapp", run_whatsapp)
            connector_registry.register(connector)
            threads.append(connector.start(workflow))
        else:
            logger.error("WhatsApp connector requested but not available")

    # Start Slack bot in thread
    if run_slack_flag:
        if SLACK_AVAILABLE:
            connector = ConnectorApplication("slack", run_slack)
            connector_registry.register(connector)
            threads.append(connector.start(workflow))
        else:
            logger.error("Slack connector requested but not available")

    # Start Signal polling loop in thread
    if run_signal_flag:
        if SIGNAL_AVAILABLE:
            connector = ConnectorApplication("signal", run_signal)
            connector_registry.register(connector)
            threads.append(connector.start(workflow))
        else:
            logger.error("Signal connector requested but not available")

    # Start API in thread (Teams / LINE / KakaoTalk webhooks are mounted on the same app)
    if run_api_flag:
        connector = ConnectorApplication("api", lambda _workflow: run_api())
        connector_registry.register(connector)
        threads.append(connector.start(workflow))

    # Start Coding Service in thread
    if run_coding_service_flag:
        try:
            logger.info("Initializing coding service thread...")
            t = threading.Thread(
                target=run_coding_service, args=(workflow,), daemon=True
            )
            threads.append(t)
            t.start()
            logger.info("Coding service thread started")
        except Exception as e:
            logger.error(f"Failed to start coding service thread: {e}", exc_info=True)

    readiness = connector_registry.wait_ready(
        float(os.getenv("CONNECTOR_READY_TIMEOUT", "15"))
    )
    for name, ready in readiness.items():
        health = connector_registry.get(name).health()
        log = logger.info if ready else logger.warning
        log(
            "Connector %s readiness=%s state=%s thread_alive=%s",
            name,
            ready,
            health.state.value,
            health.thread_alive,
        )

    if enable_proactive:
        proactive_connectors = connector_registry.outbound()
        if proactive_connectors:
            try:
                from services.proactive_messaging import ProactiveMessagingService

                proactive_service = ProactiveMessagingService(
                    workflow, connectors=proactive_connectors
                )
                proactive_service.start()
                logger.info(
                    "✅ Proactive messaging started for: %s",
                    ", ".join(sorted(proactive_connectors)),
                )
            except Exception as e:
                logger.error(
                    "❌ Failed to start proactive messaging: %s", e, exc_info=True
                )
        else:
            logger.info("Proactive messaging has no push-capable connector to use")

    if os.getenv("X_AUTOPOST_ENABLED", "false").lower() == "true":
        try:
            from services.x_autopost import XAutopostService, status_snapshot

            x_autopost_service = XAutopostService()
            x_autopost_service.start()
            logger.info("X autopost status: %s", status_snapshot())
        except Exception as e:
            logger.error("Failed to start X autopost service: %s", e, exc_info=True)

    if run_coder_flag:
        run_coder_interactive()

    if run_coder_batch_flag:
        if args.coder_config:
            goal, files_to_edit, repo_path, branch_name = (
                get_batch_coder_params_from_config(args.coder_config)
            )
        else:
            goal, files_to_edit, repo_path, branch_name = (
                get_batch_coder_params_from_cli(args)
            )
        validate_coder_batch_params(goal, files_to_edit, repo_path, branch_name)
        run_coder_batch(goal, files_to_edit, repo_path, branch_name)

    # Keep the process alive while connector threads run.
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if x_autopost_service:
            x_autopost_service.stop()
        if proactive_service:
            proactive_service.stop()
        connector_registry.stop_all()


if __name__ == "__main__":
    main()
