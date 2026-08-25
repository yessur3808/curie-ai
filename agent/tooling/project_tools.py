"""Registered tools for confined project inspection and modification."""

from __future__ import annotations

import asyncio
import difflib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
import uuid

from agent.tooling.contracts import ToolContext, ToolResult
from agent.tooling import policies


def _tree(root: Path, limit: int = 120) -> str:
    ignored = {
        ".git",
        ".venv",
        ".curie-rollbacks",
        "node_modules",
        "__pycache__",
        "models",
    }
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if any(part in ignored for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            continue
        try:
            policies.validate_path(path, root=root)
        except (OSError, PermissionError, ValueError):
            continue
        entries.append(str(path.relative_to(root)) + ("/" if path.is_dir() else ""))
        if len(entries) >= limit:
            entries.append("... output limited")
            break
    return "\n".join(entries) or "(empty directory)"


def _create_python_project(name: str, parent: Path) -> tuple[str, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]{1,63}", name):
        raise ValueError(
            "Project name may contain only letters, numbers, dots, dashes, and underscores"
        )
    parent.mkdir(parents=True, exist_ok=True)
    root = policies.validate_path(
        parent / name, root=parent, write=True, allow_missing=True
    )
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Project already exists and is not empty: {root}")
    root.mkdir(exist_ok=True)
    package = re.sub(r"\W+", "_", name).strip("_").lower()
    (root / package).mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / package / "__init__.py").touch(exist_ok=True)
    (root / "tests" / "test_smoke.py").write_text(
        f"def test_{package}_imports():\n    import {package}\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
        'requires-python = ">=3.10"\n\n[tool.pytest.ini_options]\npythonpath = ["."]\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    return f"Created Python project `{name}` at `{root}`.", root


def _code_context(root: Path, max_files: int = 20, max_chars: int = 50000) -> str:
    suffixes = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".md",
    }
    pieces: list[str] = []
    total = 0
    for path in policies.bounded_files(root):
        if path.suffix not in suffixes or any(
            part in {".git", ".venv", ".curie-rollbacks", "node_modules", "models"}
            for part in path.parts
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8")[:10000]
        except (OSError, UnicodeError):
            continue
        item = f"\n--- {path.relative_to(root)} ---\n{content}"
        if total + len(item) > max_chars:
            break
        pieces.append(item)
        total += len(item)
        if len(pieces) >= max_files:
            break
    return "".join(pieces)


def _apply_project_change(request: str, root: Path) -> tuple[str, list[str]]:
    from llm.manager import ask_llm

    prompt = (
        "Act as a careful coding agent. Return ONLY JSON of the form "
        '{"files":{"relative/path":"complete new file contents"},"summary":"..."}. '
        "Change at most 5 text files. Never use absolute paths, .., secrets, .env, .git, "
        "binaries, lockfiles, or generated dependency directories. Preserve unrelated code.\n"
        f"Request: {request}\nProject files:{_code_context(root)}"
    )
    raw = ask_llm(prompt, role="agent", temperature=0.1, max_tokens=6000)
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("The coding model did not return a valid change set")
    payload = json.loads(match.group(0))
    files = payload.get("files", {})
    if not isinstance(files, dict) or not 1 <= len(files) <= 5:
        raise ValueError("The proposed change set must contain 1 to 5 files")
    proposals: list[tuple[str, Path, bool, str, str]] = []
    total_bytes = 0
    for relative, content in files.items():
        if not isinstance(relative, str) or not isinstance(content, str):
            raise ValueError("Invalid generated file entry")
        relative_path = Path(relative)
        if (
            relative.startswith(".")
            or relative_path.is_absolute()
            or ".." in relative_path.parts
        ):
            raise PermissionError("Generated path escaped the project")
        target = policies.validate_path(
            root / relative_path, root=root, write=True, allow_missing=True
        )
        if len(content.encode("utf-8")) > 200_000:
            raise ValueError("Generated file is too large")
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > 1_000_000:
            raise ValueError("Generated change set is too large")
        existed = target.exists()
        old = target.read_text(encoding="utf-8") if existed else ""
        proposals.append((relative, target, existed, old, content))

    rollback_root = root / ".curie-rollbacks" / uuid.uuid4().hex
    rollback_root.mkdir(parents=True, exist_ok=False)
    previews, changed = [], []
    try:
        for relative, target, existed, old, content in proposals:
            previews.extend(
                difflib.unified_diff(
                    old.splitlines(),
                    content.splitlines(),
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                    lineterm="",
                )
            )
            if existed:
                backup = rollback_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            changed.append(relative)
    except Exception:
        for relative, target, existed, old, _ in proposals:
            if existed:
                target.write_text(old, encoding="utf-8")
            elif target.exists():
                target.unlink()
        raise
    preview = "\n".join(previews)[:20_000] or "(no textual diff)"
    text = (
        f"Applied the approved change to: {', '.join(changed)}. "
        f"Rollback data: {rollback_root.relative_to(root)}. "
        f"{payload.get('summary', '')}\n\nPatch preview:\n```diff\n{preview}\n```"
    ).strip()
    return text, changed


class InspectProjectTool:
    name = "inspect_project"
    read_only = True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        root = policies.action_root(
            context.internal_id, dict(context.profile), params.get("path")
        )
        return ToolResult(
            f"Files under `{root}`:\n```\n{_tree(root)}\n```", {"root": str(root)}
        )


class CreateDirectoryTool:
    name = "create_directory"
    read_only = False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        parent = (
            policies.PROJECTS_ROOT
            if policies.is_master(context.internal_id)
            else policies.user_project_root(context.internal_id)
        )
        parent.mkdir(parents=True, exist_ok=True)
        target = policies.validate_path(
            parent / str(params["name"]),
            root=parent,
            write=True,
            allow_missing=True,
        )
        target.mkdir(parents=True, exist_ok=False)
        return ToolResult(f"Created directory `{target}`.", {"path": str(target)})


class CreatePythonProjectTool:
    name = "create_python_project"
    read_only = False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        parent = (
            policies.PROJECTS_ROOT
            if policies.is_master(context.internal_id)
            else policies.user_project_root(context.internal_id)
        )
        text, root = _create_python_project(str(params["name"]), parent)
        policies.remember_active_project(context.internal_id, str(params["name"]))
        return ToolResult(text, {"path": str(root)})


class RunTestsTool:
    name = "run_tests"
    read_only = True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        root = policies.action_root(
            context.internal_id, dict(context.profile), params.get("path")
        )
        pytest = root / ".venv" / "bin" / "pytest"
        argv = [str(pytest), "-q"] if pytest.exists() else ["pytest", "-q"]
        text = await asyncio.to_thread(policies.run_sandboxed, argv, root)
        return ToolResult(text, {"root": str(root), "command": argv, "exit_status": 0})


class ProjectChangeTool:
    name = "project_change"
    read_only = False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        root = policies.action_root(
            context.internal_id, dict(context.profile), params.get("path")
        )
        text, changed = await asyncio.to_thread(
            _apply_project_change, str(params["request"]), root
        )
        if params.get("run_tests"):
            pytest = root / ".venv" / "bin" / "pytest"
            argv = [str(pytest), "-q"] if pytest.exists() else ["pytest", "-q"]
            test_result = await asyncio.to_thread(policies.run_sandboxed, argv, root)
            text += f"\n\nTest run:\n{test_result}"
        data = {"root": str(root), "changed_files": changed}
        if params.get("run_tests"):
            data.update({"command": argv, "exit_status": 0})
        return ToolResult(text, data)
