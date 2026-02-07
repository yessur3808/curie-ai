#!/usr/bin/env python3
"""
Setup Verification Script for C.U.R.I.E. AI

This script verifies that all required dependencies and configurations are in place
before running the application. It helps diagnose common setup issues.
"""

import sys
import os
import importlib.util
from typing import List, Tuple

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def check_python_version() -> bool:
    """Check if Python version meets requirements (>=3.10)"""
    version = sys.version_info
    if version.major == 3 and version.minor >= 10:
        print(f"{GREEN}✓{RESET} Python version: {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        print(f"{RED}✗{RESET} Python version {version.major}.{version.minor}.{version.micro} is too old. Required: 3.10 or higher")
        return False

def check_module(module_name: str, import_name: str = None) -> bool:
    """Check if a Python module is installed"""
    if import_name is None:
        import_name = module_name
    
    try:
        importlib.import_module(import_name)
        print(f"{GREEN}✓{RESET} {module_name} is installed")
        return True
    except ImportError:
        print(f"{RED}✗{RESET} {module_name} is NOT installed")
        return False

def check_env_file() -> bool:
    """Check if .env file exists"""
    if os.path.exists('.env'):
        print(f"{GREEN}✓{RESET} .env file exists")
        return True
    else:
        print(f"{YELLOW}⚠{RESET} .env file not found (you may need to copy .env.example)")
        return False

def check_database_config() -> Tuple[bool, List[str]]:
    """Check if database environment variables are set"""
    required_vars = [
        'POSTGRES_HOST',
        'POSTGRES_PORT', 
        'POSTGRES_DB',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD',
        'MONGO_URI'
    ]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
    
    if missing:
        print(f"{YELLOW}⚠{RESET} Database config incomplete. Missing: {', '.join(missing)}")
        return False, missing
    else:
        print(f"{GREEN}✓{RESET} Database configuration present")
        return True, []

def main():
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}C.U.R.I.E. Setup Verification{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    # Track overall status
    all_passed = True
    
    # Check Python version
    print(f"\n{BLUE}Checking Python Version...{RESET}")
    if not check_python_version():
        all_passed = False
    
    # Check critical dependencies
    print(f"\n{BLUE}Checking Critical Dependencies...{RESET}")
    critical_modules = [
        ('python-dotenv', 'dotenv'),
        ('python-telegram-bot', 'telegram'),
        ('pytz', 'pytz'),
        ('psycopg2-binary', 'psycopg2'),
        ('pymongo', 'pymongo'),
        ('fastapi', 'fastapi'),
        ('uvicorn', 'uvicorn'),
    ]
    
    for module_name, import_name in critical_modules:
        if not check_module(module_name, import_name):
            all_passed = False
    
    # Check optional dependencies
    print(f"\n{BLUE}Checking Optional Dependencies...{RESET}")
    optional_modules = [
        ('llama-cpp-python', 'llama_cpp'),
        ('python-weather', 'python_weather'),
        ('discord.py', 'discord'),
        ('GitPython', 'git'),
        ('PyGithub', 'github'),
    ]
    
    optional_missing = []
    for module_name, import_name in optional_modules:
        if not check_module(module_name, import_name):
            optional_missing.append(module_name)
    
    # Check configuration
    print(f"\n{BLUE}Checking Configuration...{RESET}")
    
    # Load .env if present
    try:
        from dotenv import load_dotenv
    except ImportError:
        print(f"{YELLOW}⚠ python-dotenv is not installed; skipping .env loading.{RESET}")
    else:
        try:
            load_dotenv()
        except Exception as exc:
            print(f"{YELLOW}⚠ Failed to load .env file: {exc}{RESET}")
    
    check_env_file()
    db_ok, missing_vars = check_database_config()
    if not db_ok:
        all_passed = False
    
    # Print summary
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}Summary{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    if all_passed and not optional_missing:
        print(f"{GREEN}✓ All checks passed! You're ready to run C.U.R.I.E.{RESET}\n")
        print(f"Start the application with:")
        print(f"  python main.py --telegram  # or --discord, --api, etc.")
        return 0
    elif all_passed:
        print(f"{YELLOW}⚠ Core setup is complete, but some optional features are unavailable.{RESET}\n")
        if optional_missing:
            print(f"Optional modules not installed: {', '.join(optional_missing)}")
        print(f"\nYou can start the application, but some features may not work:")
        print(f"  python main.py --telegram  # or --discord, --api, etc.")
        return 0
    else:
        print(f"{RED}✗ Setup incomplete. Please address the issues above.{RESET}\n")
        print(f"To install missing dependencies, run:")
        print(f"  pip install -r requirements.txt")
        print(f"\nTo configure environment variables:")
        print(f"  1. Copy .env.example to .env")
        print(f"  2. Edit .env and set your configuration values")
        return 1

if __name__ == "__main__":
    sys.exit(main())
