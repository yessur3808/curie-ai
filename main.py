# main.py

import argparse
import os
import threading
import sys
import json
import logging
import time
import re

# Check for critical dependencies early to provide helpful error messages
try:
    from connectors.telegram import (
        start_telegram_bot,
        set_workflow as set_telegram_workflow,
    )
    from connectors.api import app as fastapi_app, set_workflow as set_api_workflow
    from memory import init_memory
    from llm import manager
    import uvicorn

    from agent.core import Agent
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
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Log the configuration for verification
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured with level: {log_level}")


def load_all_agents():
    agents = {}
    for persona_info in list_available_personas():
        persona = load_persona(persona_info["filename"])
        name = persona["name"]
        agents[name] = Agent(persona=persona)
    return agents


def load_default_agent(persona_filename=None):
    persona = load_persona(filename=persona_filename)
    return Agent(persona=persona)


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
            d
            for d in dirs
            if d not in _SKIP_DIRS and not d.endswith(".egg-info")
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
            confirm = input(
                f"⚠️  That's a large number of files ({len(files_to_edit)}). "
                "Proceed? [y/N]: "
            ).strip().lower()
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
    parser.add_argument("--slack", action="store_true", help="Run Slack connector")
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
        # No connector was explicitly requested — default to the REST API so
        # that a bare ``python main.py`` (or an unconfigured .env) still works.
        logger.info(
            "No connector specified — defaulting to REST API (http://0.0.0.0:8000).\n"
            "  Tip: pass --telegram, --discord, --all, etc. or set RUN_* in .env.\n"
            "  Example: python main.py --api --telegram"
        )
        run_api_flag = True
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
        max_history=5,
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

    # Initialize proactive messaging service (only if any connectors are running)
    proactive_service = None
    enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"

    if enable_proactive and (
        run_telegram_flag or run_discord_flag or run_whatsapp_flag or run_api_flag
    ):
        try:
            logger.info("Initializing proactive messaging service...")
            # Proactive connectors are not yet registered in this process, so do not start the service.
            # This avoids pretending the service might have started while no connectors are actually wired.
            logger.info(
                "Proactive messaging service not started because no connectors are registered yet."
            )
        except Exception as e:
            logger.error(
                f"❌ Failed to start proactive messaging service: {e}", exc_info=True
            )
    elif not enable_proactive:
        logger.info(
            "ℹ️  Proactive messaging is disabled via ENABLE_PROACTIVE_MESSAGING env variable"
        )

    # Start Discord bot in thread
    if run_discord_flag:
        if DISCORD_AVAILABLE:
            t = threading.Thread(target=run_discord, args=(workflow,), daemon=True)
            threads.append(t)
            t.start()
        else:
            logger.error("Discord connector requested but not available")

    # Start WhatsApp bot in thread
    if run_whatsapp_flag:
        if WHATSAPP_AVAILABLE:
            t = threading.Thread(target=run_whatsapp, args=(workflow,), daemon=True)
            threads.append(t)
            t.start()
        else:
            logger.error("WhatsApp connector requested but not available")

    # Start Slack bot in thread
    if run_slack_flag:
        if SLACK_AVAILABLE:
            t = threading.Thread(target=run_slack, args=(workflow,), daemon=True)
            threads.append(t)
            t.start()
        else:
            logger.error("Slack connector requested but not available")

    # Start Signal polling loop in thread
    if run_signal_flag:
        if SIGNAL_AVAILABLE:
            t = threading.Thread(target=run_signal, args=(workflow,), daemon=True)
            threads.append(t)
            t.start()
        else:
            logger.error("Signal connector requested but not available")

    # Start API in thread (Teams / LINE / KakaoTalk webhooks are mounted on the same app)
    if run_api_flag:
        t = threading.Thread(target=run_api, daemon=True)
        threads.append(t)
        t.start()

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

    # Start Telegram bot last (blocking) - keeps main thread alive
    if run_telegram_flag:
        run_telegram(workflow)
    else:
        # If Telegram is not running, join other threads to prevent main from exiting
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            if proactive_service:
                proactive_service.stop()


if __name__ == "__main__":
    main()
