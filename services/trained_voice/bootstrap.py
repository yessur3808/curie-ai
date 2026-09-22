"""Load Curie's existing environment before importing its model services."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(os.environ.get("CURIE_ROOT", Path(__file__).resolve().parents[2])).resolve()
load_dotenv(ROOT / ".env")
os.chdir(ROOT)
