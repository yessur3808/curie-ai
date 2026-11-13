#!/usr/bin/env python3
"""Load secret references then exec without writing a credential environment file."""

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    from services.vault_runtime import load_runtime_secrets

    load_dotenv(ROOT / ".env")
    instance = os.getenv("CURIE_INSTANCE", "curie")
    if not instance.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid instance")
    load_dotenv(ROOT / "instances" / f"{instance}.env", override=True)
    if not os.getenv("CURIE_VAULT_CONFIG"):
        raise RuntimeError("Vault configuration is required for this launcher")
    load_runtime_secrets()
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        raise ValueError("A command is required")
    os.execvpe(args[0], args, os.environ)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Vault startup failed (" + type(error).__name__ + ").", file=sys.stderr)
        raise SystemExit(1)
