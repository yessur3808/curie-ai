"""Reusable filesystem and command-execution policies for local tools."""

from __future__ import annotations

import os
from pathlib import Path
import re
import resource
import signal
import shutil
import subprocess
import tempfile
import uuid

from agent.tooling.errors import SandboxCommandError


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(os.getenv("CURIE_WORKSPACE_ROOT", str(REPO_ROOT))).resolve()
PROJECTS_ROOT = Path(
    os.getenv(
        "CURIE_PROJECTS_ROOT",
        os.getenv("PROJECTS_ROOT", str(WORKSPACE_ROOT / "projects")),
    )
).resolve()

SECRET_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_ed25519",
        "known_hosts",
    }
)
SECRET_PARTS = frozenset({".ssh", ".gnupg", "browser profiles", "credentials"})
ARCHIVE_SUFFIXES = frozenset({".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z"})
MAX_FILES = 2_000
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 25_000_000
MAX_OUTPUT_BYTES = 64_000
IGNORED_TREES = frozenset(
    {".git", ".venv", ".curie-rollbacks", "node_modules", "models", "__pycache__"}
)


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_path(raw: str | None, *, projects: bool = False) -> Path:
    root = PROJECTS_ROOT if projects else WORKSPACE_ROOT
    candidate = (root / (raw or ".")).resolve()
    if not within(candidate, root):
        raise PermissionError(f"Path is outside Curie's allowed root: {root}")
    validate_path(candidate, root=root, allow_missing=True)
    return candidate


def _is_secret(path: Path) -> bool:
    lowered = [part.casefold() for part in path.parts]
    return path.name.casefold() in SECRET_NAMES or any(
        part in SECRET_PARTS or "token" in part for part in lowered
    )


def validate_path(
    path: Path,
    *,
    root: Path,
    write: bool = False,
    allow_missing: bool = False,
) -> Path:
    """Resolve and validate one path immediately before filesystem access."""
    root = root.resolve(strict=True)
    lexical = Path(os.path.abspath(path))
    candidate = lexical.resolve(strict=not allow_missing)
    if not within(candidate, root):
        raise PermissionError("Path escaped its allowed filesystem root")
    relative = candidate.relative_to(root)
    if _is_secret(relative):
        raise PermissionError("Secret and credential paths are denied by default")
    if candidate.suffix.casefold() in ARCHIVE_SUFFIXES:
        raise PermissionError("Archive access requires a dedicated bounded extractor")
    if candidate.exists():
        stat = candidate.stat()
        if candidate.is_file() and stat.st_nlink > 1:
            raise PermissionError("Hard-linked files are denied inside the sandbox")
        if candidate.is_file() and stat.st_size > MAX_FILE_BYTES:
            raise ValueError("File exceeds the sandbox size limit")
    if write:
        current = lexical
        while current != root and within(current, root):
            if current.is_symlink():
                raise PermissionError("Writing through symbolic links is denied")
            current = current.parent
    return candidate


def bounded_files(root: Path):
    """Yield safe files while enforcing aggregate traversal resource limits."""
    root = root.resolve(strict=True)
    count = total = 0
    for candidate in sorted(root.rglob("*")):
        if any(part in IGNORED_TREES for part in candidate.relative_to(root).parts):
            continue
        if candidate.is_symlink():
            continue
        if not candidate.is_file():
            continue
        try:
            candidate = validate_path(candidate, root=root)
            size = candidate.stat().st_size
        except (OSError, PermissionError, ValueError):
            continue
        count += 1
        total += size
        if count > MAX_FILES:
            raise ValueError("Project exceeds the sandbox file-count limit")
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Project exceeds the sandbox total-byte limit")
        yield candidate


def is_master(internal_id: str) -> bool:
    configured = os.getenv("MASTER_USER_ID", "").strip()
    return bool(configured) and configured == str(internal_id)


def user_project_root(internal_id: str) -> Path:
    root = (PROJECTS_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", str(internal_id))).resolve()
    if not within(root, PROJECTS_ROOT):
        raise PermissionError("Invalid user project root")
    root.mkdir(parents=True, exist_ok=True)
    return root


def action_root(internal_id: str, profile: dict, raw: str | None = None) -> Path:
    if is_master(internal_id):
        if profile.get("active_project") and (raw is None or raw == "."):
            candidate = (PROJECTS_ROOT / str(profile["active_project"])).resolve()
            if within(candidate, PROJECTS_ROOT):
                return candidate
        return safe_path(raw)
    root = user_project_root(internal_id)
    candidate = (root / (profile.get("active_project") or raw or ".")).resolve()
    if not within(candidate, root):
        raise PermissionError("Path is outside your project sandbox")
    validate_path(candidate, root=root, allow_missing=True)
    return candidate


def remember_active_project(internal_id: str, name: str) -> None:
    from memory.repositories import get_repositories

    get_repositories().profiles.update(internal_id, {"active_project": name})


def _validate_argv(argv: list[str], *, approved: bool, mutating: bool) -> None:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("Command must be a non-empty executable and argument array")
    executable = Path(argv[0]).name
    safe_read_only = {"pytest"}
    approved_tools = safe_read_only | {
        "python",
        "python3",
        "npm",
        "pnpm",
        "cargo",
        "go",
    }
    if executable not in approved_tools:
        raise PermissionError(f"Command {executable!r} is not allowlisted")
    if (mutating or executable not in safe_read_only) and not approved:
        raise PermissionError("This command requires fresh scoped approval")
    forbidden = {"-c", "--eval", "--exec", "install", "publish", "add", "remove"}
    if not approved and any(item.casefold() in forbidden for item in argv[1:]):
        raise PermissionError("Command argument requires explicit approval")
    if any("\x00" in item or "\n" in item or "\r" in item for item in argv):
        raise ValueError("Command arguments contain invalid control characters")
    if any(re.search(r"[;&|`$<>]", item) for item in argv):
        raise PermissionError("Shell syntax is forbidden in command arguments")


def _resource_limits(timeout: int):
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, timeout), max(1, timeout + 1)))
        resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
        resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))

    return apply


def run_sandboxed(
    argv: list[str],
    cwd: Path,
    timeout: int = 90,
    *,
    approved: bool = False,
    mutating: bool = False,
) -> str:
    _validate_argv(argv, approved=approved, mutating=mutating)
    cwd_path = Path(cwd)
    allowed_root = WORKSPACE_ROOT if within(cwd_path, WORKSPACE_ROOT) else PROJECTS_ROOT
    cwd = validate_path(cwd_path, root=allowed_root)
    executable = Path(argv[0]).name
    allowed_env = {"PATH", "LANG", "LC_ALL", "TZ", "TERM"}
    env = {key: value for key, value in os.environ.items() if key in allowed_env}
    env.update({"HOME": "/run/curie-task", "PYTHONDONTWRITEBYTECODE": "1"})
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("bubblewrap is required for sandboxed command execution")
    task_dir = Path(tempfile.gettempdir()) / f"curie-task-{uuid.uuid4().hex}"
    task_dir.mkdir(mode=0o700)
    sandbox = [bwrap, "--die-with-parent", "--unshare-all", "--new-session"]
    for system_path in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
        if Path(system_path).exists():
            sandbox.extend(["--ro-bind", system_path, system_path])
    sandbox.extend(
        [
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/run",
            "--bind",
            str(task_dir),
            "/run/curie-task",
            "--bind",
            str(cwd),
            str(cwd),
            "--chdir",
            str(cwd),
            "--",
        ]
    )
    try:
        process = subprocess.Popen(
            sandbox + argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
            env=env,
            start_new_session=True,
            preexec_fn=_resource_limits(timeout),
        )
        try:
            stdout, stderr = process.communicate(timeout=max(1, timeout))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            raise TimeoutError(f"Command {executable!r} exceeded {timeout} seconds")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
    output = (
        (stdout + b"\n" + stderr)[-MAX_OUTPUT_BYTES:]
        .decode("utf-8", errors="replace")
        .strip()
    )
    if process.returncode != 0:
        raise SandboxCommandError(executable, process.returncode, output)
    return f"Exit code: {process.returncode}\n{output}".strip()
