"""Exercise the contribution verifier against a real public repository."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tempfile

from services.contribution_sandbox import ContributionSandbox

REPOSITORY = "https://github.com/pypa/sampleproject"
BRANCH = "main"
PYTHON_PATH = "src/sample/simple.py"


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="curie-ci-sandbox-") as temporary:
        checkout = Path(temporary) / "sampleproject"
        _run(
            [
                "git",
                "-c",
                "credential.helper=",
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--branch",
                BRANCH,
                REPOSITORY,
                str(checkout),
            ]
        )
        base_sha = _run(["git", "rev-parse", "HEAD"], cwd=checkout)
        before = (checkout / PYTHON_PATH).read_text(encoding="utf-8")

    result = ContributionSandbox().verify(
        {
            "repository_url": REPOSITORY,
            "base_branch": BRANCH,
            "base_sha": base_sha,
            "files": [
                {
                    "path": PYTHON_PATH,
                    "before_hash": hashlib.sha256(before.encode()).hexdigest(),
                    "after": before + "\n# Curie sandbox smoke test.\n",
                }
            ],
        }
    )
    if not result.get("passed"):
        raise RuntimeError(f"Contribution sandbox smoke test failed: {result}")
    passed = {
        item["name"]
        for item in result.get("checks", [])
        if item.get("status") == "passed"
    }
    required = {
        "security_scan",
        "clone",
        "patch_lint",
        "syntax",
        "lint",
        "security",
        "project_tests",
    }
    missing = required - passed
    if missing:
        raise RuntimeError(f"Sandbox checks did not run: {sorted(missing)}")
    if result.get("network") != "disabled":
        raise RuntimeError("Contribution test container unexpectedly had networking")
    print("Contribution sandbox smoke test passed with networking disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
