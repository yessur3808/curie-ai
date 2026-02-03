# main.py

import argparse
import os
import threading
import sys
import json

from connectors.telegram import start_telegram_bot
from connectors.api import app as fastapi_app
from memory import init_memory
from llm import manager
import uvicorn

from agent.core import Agent
from utils.persona import load_persona, list_available_personas
import asyncio


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


def run_telegram(agents):
    print("Starting Telegram connector...")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    start_telegram_bot(agents)



def run_api():
    print("Starting API (FastAPI) connector on http://0.0.0.0:8000 ...")
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_level="info")

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
    parser.add_argument('--api', action='store_true', help="Run API connector (FastAPI)")
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
    run_api_env = os.getenv("RUN_API", "false").lower() == "true"
    run_coder_env = os.getenv("RUN_CODER", "false").lower() == "true"

    run_telegram_flag = args.all or args.telegram or run_telegram_env
    run_api_flag = args.all or args.api or run_api_env
    run_coder_flag = args.all or args.coder or run_coder_env
    run_coder_batch_flag = args.coder_batch

    if not (run_telegram_flag or run_api_flag or run_coder_flag or run_coder_batch_flag):
        print("Nothing to run! Use --telegram, --api, --coder, --coder-batch, --all or set RUN_* in .env.")
        sys.exit(1)
    return run_telegram_flag, run_api_flag, run_coder_flag, run_coder_batch_flag

def init_llm_and_memory(no_init):
    if not no_init:
        print("Initializing model and memory...")
        manager.preload_llama_model()
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
    args = parse_args()
    run_telegram_flag, run_api_flag, run_coder_flag, run_coder_batch_flag = determine_what_to_run(args)
    init_llm_and_memory(args.no_init)
    
    # Decide agent(s) based on persona argument/env
    persona_arg = getattr(args, "persona", None)
    if (persona_arg and persona_arg.strip().lower() == "all") or (
        not persona_arg and os.getenv("PERSONA_FILE", "").strip().lower() == "all"
    ):
        agents = load_all_agents()
        agent_or_agents = agents
    else:
        agent = load_default_agent(persona_arg)
        agent_or_agents = agent

    threads = []
    if run_telegram_flag:
        run_telegram(agent_or_agents) 

    if run_api_flag:
        t = threading.Thread(target=run_api, daemon=True)
        threads.append(t)
        t.start()

    if run_coder_flag:
        run_coder_interactive()

    if run_coder_batch_flag:
        if args.coder_config:
            goal, files_to_edit, repo_path, branch_name = get_batch_coder_params_from_config(args.coder_config)
        else:
            goal, files_to_edit, repo_path, branch_name = get_batch_coder_params_from_cli(args)
        validate_coder_batch_params(goal, files_to_edit, repo_path, branch_name)
        run_coder_batch(goal, files_to_edit, repo_path, branch_name)

    for t in threads:
        t.join()
        
        
if __name__ == "__main__":
    main()