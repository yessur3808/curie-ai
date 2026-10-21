"""Disposable, network-isolated verification for proposed GitHub patches."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class SandboxCheck:
    name: str
    status: str
    detail: str
    duration_ms: int = 0


_DANGEROUS = re.compile(
    r"(?i)(?:curl|wget)\s+[^\n]*\|\s*(?:sh|bash)|"
    r"(?:subprocess|os\.system)\s*\([^\n]*(?:rm\s+-rf|chmod\s+777)|"
    r"(?:api[_-]?key|password|secret|token)\s*[:=]\s*['\"][^'\"]{8,}"
)


def _run(
    argv: list[str], *, cwd: Path | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
    )


def _bounded_output(value: str) -> str:
    text = str(value or "").strip()
    return text[-1200:] if text else "completed without output"


class ContributionSandbox:
    """Clone a public repository, apply a bounded patch, and test it in Docker."""

    image = os.getenv(
        "CURIE_CONTRIBUTION_PYTHON_IMAGE", "curie-contribution-sandbox:py312-v1"
    )

    @staticmethod
    def _runtime() -> str:
        configured = os.getenv("CURIE_CONTRIBUTION_CONTAINER_RUNTIME", "docker").strip()
        if configured not in {"docker", "podman"}:
            raise ValueError("Contribution container runtime must be docker or podman")
        if not shutil.which(configured):
            raise RuntimeError(f"{configured} is not installed")
        return configured

    @staticmethod
    def _security_scan(files: Iterable[Mapping[str, Any]]) -> SandboxCheck:
        for item in files:
            path, after = str(item.get("path") or ""), str(item.get("after") or "")
            match = _DANGEROUS.search(after)
            if match:
                return SandboxCheck(
                    "security_scan", "failed", f"unsafe pattern in {path}"
                )
            if len(after.encode("utf-8")) > 200_000:
                return SandboxCheck(
                    "security_scan", "failed", f"oversized generated file: {path}"
                )
        return SandboxCheck(
            "security_scan", "passed", "secret and dangerous-command scan passed"
        )

    def verify(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        files = [dict(item) for item in payload.get("files", ())]
        checks: list[SandboxCheck] = [self._security_scan(files)]
        if checks[0].status != "passed":
            return {"passed": False, "checks": [asdict(item) for item in checks]}
        repository_url = str(payload.get("repository_url") or "")
        if not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", repository_url
        ):
            checks.append(
                SandboxCheck(
                    "clone", "failed", "repository URL is not a public GitHub HTTPS URL"
                )
            )
            return {"passed": False, "checks": [asdict(item) for item in checks]}
        runtime = self._runtime()
        with tempfile.TemporaryDirectory(prefix="curie-contribution-") as temporary:
            root = Path(temporary) / "repo"
            clone = _run(
                [
                    "git",
                    "-c",
                    "credential.helper=",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    str(payload["base_branch"]),
                    repository_url,
                    str(root),
                ],
                timeout=120,
            )
            if clone.returncode:
                checks.append(
                    SandboxCheck("clone", "failed", _bounded_output(clone.stdout))
                )
                return {"passed": False, "checks": [asdict(item) for item in checks]}
            checks.append(
                SandboxCheck("clone", "passed", "clean shallow clone created")
            )
            head = _run(["git", "rev-parse", "HEAD"], cwd=root)
            if head.returncode or head.stdout.strip() != str(payload["base_sha"]):
                checks.append(
                    SandboxCheck(
                        "base_commit",
                        "failed",
                        "upstream commit changed during verification",
                    )
                )
                return {"passed": False, "checks": [asdict(item) for item in checks]}
            for item in files:
                target = (root / str(item["path"])).resolve()
                if root.resolve() not in target.parents:
                    raise PermissionError(
                        "Generated path escaped the disposable checkout"
                    )
                current = target.read_text(encoding="utf-8")
                if hashlib.sha256(current.encode()).hexdigest() != str(
                    item["before_hash"]
                ):
                    checks.append(
                        SandboxCheck(
                            "base_files", "failed", f"source changed: {item['path']}"
                        )
                    )
                    return {
                        "passed": False,
                        "checks": [asdict(value) for value in checks],
                    }
                target.write_text(str(item["after"]), encoding="utf-8")
            whitespace = _run(["git", "diff", "--check"], cwd=root)
            checks.append(
                SandboxCheck(
                    "patch_lint",
                    "passed" if whitespace.returncode == 0 else "failed",
                    _bounded_output(whitespace.stdout),
                )
            )
            if whitespace.returncode:
                return {"passed": False, "checks": [asdict(item) for item in checks]}
            if not any(str(item["path"]).endswith(".py") for item in files):
                checks.append(
                    SandboxCheck(
                        "language",
                        "failed",
                        "only Python contributions currently have a hardened verification image",
                    )
                )
                return {"passed": False, "checks": [asdict(item) for item in checks]}
            mount = f"{root}:/workspace:ro"
            common = [
                runtime,
                "run",
                "--rm",
                "--network=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=256",
                "--memory=1536m",
                "--cpus=2",
                "--read-only",
                "--tmpfs=/tmp:rw,noexec,nosuid,size=128m",
                "-e",
                "PYTHONPYCACHEPREFIX=/tmp/pycache",
                "-e",
                "PYTHONPATH=/workspace/src:/workspace",
                "-v",
                mount,
                "-w",
                "/workspace",
                self.image,
            ]
            changed_paths = [str(item["path"]) for item in files]
            python_paths = [path for path in changed_paths if path.endswith(".py")]
            commands = [
                ("syntax", ["python", "-m", "compileall", "-q", *python_paths]),
                ("lint", ["ruff", "check", "--no-cache", *python_paths]),
                ("security", ["bandit", "-q", *python_paths]),
                (
                    "project_tests",
                    [
                        "python",
                        "-m",
                        "pytest",
                        "-q",
                        "-o",
                        "cache_dir=/tmp/pytest-cache",
                    ],
                ),
            ]
            for name, command in commands:
                result = _run(
                    common + command,
                    timeout=int(os.getenv("CURIE_CONTRIBUTION_TEST_TIMEOUT", "180")),
                )
                checks.append(
                    SandboxCheck(
                        name,
                        "passed" if result.returncode == 0 else "failed",
                        _bounded_output(result.stdout),
                    )
                )
                if result.returncode:
                    return {
                        "passed": False,
                        "checks": [asdict(item) for item in checks],
                    }
        return {
            "passed": True,
            "runtime": runtime,
            "image": self.image,
            "network": "disabled",
            "checks": [asdict(item) for item in checks],
        }


contribution_sandbox = ContributionSandbox()


__all__ = ["ContributionSandbox", "SandboxCheck", "contribution_sandbox"]
