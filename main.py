# main.py

import argparse
import os
import threading
import sys
import json
import logging
import time

# Check for critical dependencies early to provide helpful error messages
try:
    from connectors.telegram import start_telegram_bot, set_workflow as set_telegram_workflow
    from connectors.api import app as fastapi_app, set_workflow as set_api_workflow
    from memory import init_memory
    from llm import manager
    import uvicorn

    from agent.core import Agent
    from agent.chat_workflow import ChatWorkflow
    from services.proactive_messaging import ProactiveMessagingService
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

# Import Discord connector (optional - may not be installed)
try:
    from connectors.discord_bot import start_discord_bot, set_workflow as set_discord_workflow
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    logger.warning("Discord connector not available (discord.py not installed)")

# Import WhatsApp connector (optional - may not be installed)
try:
    from connectors.whatsapp import start_whatsapp_bot, set_workflow as set_whatsapp_workflow
    WHATSAPP_AVAILABLE = True
except ImportError:
    WHATSAPP_AVAILABLE = False
    logger.warning("WhatsApp connector not available (whatsapp-web.py not installed)")

def configure_logging():
    """
    Configure logging for the application at startup.
    
    Sets up logging to capture all log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    with a consistent format across all modules. This ensures that important logs,
    including security-related messages from URL validation, are properly captured.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv(
        "LOG_FORMAT",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Log the configuration for verification
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured with level: {log_level}")


def load_all_agents():
    agents = {}
    for persona_info in list_available_personas():
        persona = load_persona(persona_info['filename'])
        name = persona['name']
        agents[name] = Agent(persona=persona)
    return agents


def load_default_agent(persona_filename=None):
    persona = load_persona(filename=persona_filename)
    return Agent(persona=persona)

# --- Helper: Find all files recursively ---
def find_all_files(repo_path, exts=None):
    all_files = []
    for root, dirs, files in os.walk(repo_path):
        for f in files:
            rel_path = os.path.relpath(os.path.join(root, f), repo_path)
            if exts is None or any(rel_path.endswith(ext) for ext in exts):
                all_files.append(rel_path)
    return all_files

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
        logger.error("WhatsApp connector is not available. Install whatsapp-web.py first.")
        return
    
    print("Starting WhatsApp connector...")
    
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    start_whatsapp_bot(workflow)



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
    goal = input("Describe the code enhancement goal: ").strip()
    repo_path = input("Enter local repo path (absolute or relative): ").strip()
    branch_name = input("Enter the branch name to use: ").strip()
    files = input("Comma-separated filenames to edit (relative to repo): ").strip()
    files_to_edit = [f.strip() for f in files.split(",") if f.strip()]
    print(f"Running code enhancement for files: {files_to_edit} ...")
    result = apply_code_change(goal, files_to_edit, repo_path, branch_name)
    print("\n---\nResult:\n")
    print("Branch:", result[0])
    print("Files changed:", list(result[1].keys()))
    print("PR URL:", result[2])

def run_coder_batch(goal, files_to_edit, repo_path, branch_name):
    print("Starting Coder skill (batch mode)...")
    from agent.skills.coder import apply_code_change
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
    parser.add_argument('--telegram', action='store_true', help="Run Telegram connector")
    parser.add_argument('--discord', action='store_true', help="Run Discord connector")
    parser.add_argument('--whatsapp', action='store_true', help="Run WhatsApp connector")
    parser.add_argument('--api', action='store_true', help="Run API connector (FastAPI)")
    parser.add_argument('--coding-service', action='store_true', help="Run standalone coding service")
    parser.add_argument('--coder', action='store_true', help="Run coder/PR skill (interactive)")
    parser.add_argument('--coder-batch', action='store_true', help="Run coder in batch mode (non-interactive)")
    parser.add_argument('--coder-config', type=str, help="JSON file with coder batch parameters")
    parser.add_argument('--coder-goal', type=str, help="Goal for coder batch mode")
    parser.add_argument('--coder-files', type=str, help="Comma-separated file list for coder batch mode")
    parser.add_argument('--coder-repo', type=str, help="Repo path for coder batch mode")
    parser.add_argument('--coder-branch', type=str, help="Branch name for coder batch mode")
    parser.add_argument('--all', action='store_true', help="Run all connectors")
    parser.add_argument('--no-init', action='store_true', help="Skip model preload and memory init")
    parser.add_argument('--persona', type=str, help="Filename of persona to use (in assets/personality)")
    return parser.parse_args()

# --- Config Determination ---
def determine_what_to_run(args):
    run_telegram_env = os.getenv("RUN_TELEGRAM", "false").lower() == "true"
    run_discord_env = os.getenv("RUN_DISCORD", "false").lower() == "true"
    run_whatsapp_env = os.getenv("RUN_WHATSAPP", "false").lower() == "true"
    run_api_env = os.getenv("RUN_API", "false").lower() == "true"
    run_coder_env = os.getenv("RUN_CODER", "false").lower() == "true"
    run_coding_service_env = os.getenv("RUN_CODING_SERVICE", "false").lower() == "true"

    run_telegram_flag = args.all or args.telegram or run_telegram_env
    run_discord_flag = args.all or args.discord or run_discord_env
    run_whatsapp_flag = args.all or args.whatsapp or run_whatsapp_env
    run_api_flag = args.all or args.api or run_api_env
    run_coder_flag = args.all or args.coder or run_coder_env
    run_coder_batch_flag = args.coder_batch
    run_coding_service_flag = args.coding_service or run_coding_service_env

    if not (run_telegram_flag or run_discord_flag or run_whatsapp_flag or run_api_flag or run_coder_flag or run_coder_batch_flag or run_coding_service_flag):
        print("Nothing to run! Use --telegram, --discord, --whatsapp, --api, --coder, --coder-batch, --coding-service, --all or set RUN_* in .env.")
        sys.exit(1)
    return run_telegram_flag, run_discord_flag, run_whatsapp_flag, run_api_flag, run_coder_flag, run_coder_batch_flag, run_coding_service_flag

def init_llm_and_memory(no_init):
    if not no_init:
        print("Initializing model and memory...")
        try:
            manager.preload_llama_model()
        except (FileNotFoundError, ValueError) as e:
            logger.warning(f"⚠️  LLM model unavailable: {e}")
            logger.warning("Continuing without LLM - text responses will be placeholders")
        except Exception as e:
            logger.warning(f"⚠️  Unexpected LLM error: {e}")
            logger.warning("Continuing without LLM")
        init_memory()

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
        if ':' in files_arg:
            ext_part = files_arg.split(":", 1)[1]
            exts = [f".{e.strip()}" if not e.startswith('.') else e.strip() for e in ext_part.split(",") if e.strip()]
        if not repo_path:
            print("Error: Must supply repo_path with files_to_edit=all or all:ext")
            sys.exit(1)
        files_to_edit = find_all_files(repo_path, exts)
        print(f"Discovered {len(files_to_edit)} files to edit in {repo_path}.")
    else:
        files_to_edit = [f.strip() for f in (files_arg or [])] if isinstance(files_arg, list) else [f.strip() for f in (files_arg or "").split(",") if f.strip()]
    return goal, files_to_edit, repo_path, branch_name

def get_batch_coder_params_from_cli(args):
    files_arg = args.coder_files
    repo_path = args.coder_repo
    if files_arg and files_arg.strip().lower().startswith("all"):
        exts = None
        if ':' in files_arg:
            ext_part = files_arg.split(":", 1)[1]
            exts = [f".{e.strip()}" if not e.startswith('.') else e.strip() for e in ext_part.split(",") if e.strip()]
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
    if not branch_name:
        missing.append("branch_name")
    if missing:
        print(f"Error: Missing batch coder parameters: {', '.join(missing)}")
        sys.exit(1)

# --- Main Orchestration ---
def main():
    # Configure logging first thing to capture all logs
    configure_logging()
    
    args = parse_args()
    run_telegram_flag, run_discord_flag, run_whatsapp_flag, run_api_flag, run_coder_flag, run_coder_batch_flag, run_coding_service_flag = determine_what_to_run(args)
    init_llm_and_memory(args.no_init)
    
    # Load persona and initialize ChatWorkflow
    persona_arg = getattr(args, "persona", None)
    persona = load_persona(filename=persona_arg)
    workflow = ChatWorkflow(persona=persona, max_history=5, enable_small_talk=False)
    logger.info(f"✅ ChatWorkflow initialized with persona: {workflow.persona.get('name')}")
    
    # Share workflow with connectors
    if run_telegram_flag or run_discord_flag or run_whatsapp_flag or run_api_flag or run_coding_service_flag:
        set_telegram_workflow(workflow)
        set_api_workflow(workflow)
        if DISCORD_AVAILABLE:
            set_discord_workflow(workflow)
        if WHATSAPP_AVAILABLE:
            set_whatsapp_workflow(workflow)

    threads = []
    
    # Initialize proactive messaging service (only if any connectors are running)
    proactive_service = None
    enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"
    
    if enable_proactive and (run_telegram_flag or run_discord_flag or run_whatsapp_flag or run_api_flag):
        try:
            logger.info("Initializing proactive messaging service...")
            # Create agent for proactive messaging
            agent = Agent(persona=persona)
            
            # Build connector map for proactive messaging
            connectors = {}
            # We'll populate this as connectors are initialized
            # Note: do not start the service unless at least one connector is registered
            
            # Get check interval from env (default: 3600 seconds = 1 hour)
            check_interval = int(os.getenv("PROACTIVE_CHECK_INTERVAL", "3600"))
            
            if not connectors:
                logger.info(
                    "Proactive messaging service not started because no connectors are registered yet."
                )
            else:
                proactive_service = ProactiveMessagingService(agent=agent, connectors=connectors)
                proactive_service.check_interval = check_interval
                proactive_service.start()
                logger.info(f"✅ Proactive messaging service started (check interval: {check_interval}s)")
        except Exception as e:
            logger.error(f"❌ Failed to start proactive messaging service: {e}", exc_info=True)
    elif not enable_proactive:
        logger.info("ℹ️  Proactive messaging is disabled via ENABLE_PROACTIVE_MESSAGING env variable")
    
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

    # Start API in thread
    if run_api_flag:
        t = threading.Thread(target=run_api, daemon=True)
        threads.append(t)
        t.start()
    
    # Start Coding Service in thread
    if run_coding_service_flag:
        try:
            logger.info("Initializing coding service thread...")
            t = threading.Thread(target=run_coding_service, args=(workflow,), daemon=True)
            threads.append(t)
            t.start()
            logger.info("Coding service thread started")
        except Exception as e:
            logger.error(f"Failed to start coding service thread: {e}", exc_info=True)

    if run_coder_flag:
        run_coder_interactive()

    if run_coder_batch_flag:
        if args.coder_config:
            goal, files_to_edit, repo_path, branch_name = get_batch_coder_params_from_config(args.coder_config)
        else:
            goal, files_to_edit, repo_path, branch_name = get_batch_coder_params_from_cli(args)
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