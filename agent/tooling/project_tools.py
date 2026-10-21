"""Registered tools for confined project inspection and modification."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
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


def _propose_project_change(request: str, root: Path) -> dict[str, Any]:
    """Generate and validate a code proposal without touching the project."""
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
    proposals: list[dict[str, Any]] = []
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
        if len(content.encode("utf-8")) > 50_000:
            raise ValueError("Generated file is too large")
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > 100_000:
            raise ValueError("Generated change set is too large")
        existed = target.exists()
        old = target.read_text(encoding="utf-8") if existed else ""
        proposals.append(
            {
                "path": relative,
                "existed": existed,
                "before": old,
                "after": content,
                "before_hash": hashlib.sha256(old.encode("utf-8")).hexdigest(),
            }
        )

    previews: list[str] = []
    for item in proposals:
        previews.extend(
            difflib.unified_diff(
                str(item["before"]).splitlines(),
                str(item["after"]).splitlines(),
                fromfile=f"a/{item['path']}",
                tofile=f"b/{item['path']}",
                lineterm="",
            )
        )
    diff = "\n".join(previews) or "(no textual diff)"
    return {
        "root": str(root.resolve()),
        "request": str(request)[:4000],
        "summary": str(payload.get("summary") or "Proposed code changes.")[:1000],
        "files": proposals,
        "diff": f"```diff\n{diff}\n```",
    }


def _apply_project_proposal(
    proposal: Mapping[str, Any], root: Path
) -> tuple[str, list[str]]:
    """Apply an already-approved proposal after verifying every source hash."""
    expected_root = Path(str(proposal.get("root") or "")).resolve()
    if expected_root != root.resolve():
        raise PermissionError("The selected project changed after approval")
    proposals: list[tuple[str, Path, bool, str, str]] = []
    for item in list(proposal.get("files") or []):
        relative = str(item.get("path") or "")
        relative_path = Path(relative)
        if (
            not relative
            or relative.startswith(".")
            or relative_path.is_absolute()
            or ".." in relative_path.parts
        ):
            raise PermissionError("An approved path escaped the project")
        target = policies.validate_path(
            root / relative_path, root=root, write=True, allow_missing=True
        )
        existed = target.exists()
        old = target.read_text(encoding="utf-8") if existed else ""
        if existed != bool(item.get("existed")) or hashlib.sha256(
            old.encode("utf-8")
        ).hexdigest() != str(item.get("before_hash") or ""):
            from agent.tooling.errors import ToolExecutionError

            raise ToolExecutionError(
                f"Source hash changed after preview: {relative}",
                user_message=(
                    f"`{relative}` changed after the preview, so I did not apply anything. "
                    "Please ask me to generate a fresh preview."
                ),
            )
        content = str(item.get("after") or "")
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
    preview = "\n".join(previews) or "(no textual diff)"
    text = (
        f"Applied the approved change to: {', '.join(changed)}. "
        f"Rollback data: {rollback_root.relative_to(root)}. "
        f"{proposal.get('summary', '')}\n\nPatch preview (applied):\n```diff\n{preview}\n```"
    ).strip()
    return text, changed


def _apply_project_change(request: str, root: Path) -> tuple[str, list[str]]:
    """Compatibility helper: propose then apply in one local call.

    Chat-facing execution never uses this shortcut; it is retained for direct
    library callers and tests. The registered tool requires an approved preview.
    """
    return _apply_project_proposal(_propose_project_change(request, root), root)


def _approved_root(context: ToolContext, raw: Any) -> Path:
    """Revalidate the exact preview root against the owner's current sandbox."""
    root = Path(str(raw or "")).resolve()
    allowed = (
        policies.WORKSPACE_ROOT
        if policies.is_master(context.internal_id)
        else policies.user_project_root(context.internal_id)
    )
    return policies.validate_path(root, root=allowed, write=True, allow_missing=False)


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
        from services.action_previews import preview_store

        preview = preview_store.load(
            context.internal_id,
            str(params.get("preview_id") or ""),
            expected_hash=str(params.get("preview_hash") or ""),
            expected_action=self.name,
        )
        proposal = dict(preview["payload"])
        root = _approved_root(context, proposal.get("root"))
        text, changed = await asyncio.to_thread(_apply_project_proposal, proposal, root)
        if proposal.get("run_tests"):
            pytest = root / ".venv" / "bin" / "pytest"
            argv = [str(pytest), "-q"] if pytest.exists() else ["pytest", "-q"]
            test_result = await asyncio.to_thread(policies.run_sandboxed, argv, root)
            text += f"\n\nTest run:\n{test_result}"
        preview_store.consume(
            context.internal_id,
            str(params["preview_id"]),
            expected_hash=str(params["preview_hash"]),
        )
        data = {"root": str(root), "changed_files": changed}
        if proposal.get("run_tests"):
            data.update({"command": argv, "exit_status": 0})
        return ToolResult(text, data)


class OrganizeFilesTool:
    name = "organize_files"
    read_only = False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from services.action_previews import preview_store

        preview = preview_store.load(
            context.internal_id,
            str(params.get("preview_id") or ""),
            expected_hash=str(params.get("preview_hash") or ""),
            expected_action=self.name,
        )
        payload = dict(preview["payload"])
        root = _approved_root(context, payload.get("root"))
        moved: list[str] = []
        for item in list(payload.get("moves") or []):
            source = policies.validate_path(root / str(item["source"]), root=root)
            target = policies.validate_path(
                root / str(item["destination"]),
                root=root,
                write=True,
                allow_missing=True,
            )
            if not source.is_file() or hashlib.sha256(
                source.read_bytes()
            ).hexdigest() != str(item["before_hash"]):
                from agent.tooling.errors import ToolExecutionError

                raise ToolExecutionError(
                    f"Source hash changed after preview: {item['source']}",
                    user_message=(
                        f"`{item['source']}` changed after the preview, so I moved nothing. "
                        "Please generate a fresh preview."
                    ),
                )
            if target.exists() and target != source:
                raise FileExistsError(
                    f"Destination already exists: {item['destination']}"
                )
        for item in list(payload.get("moves") or []):
            source = root / str(item["source"])
            target = root / str(item["destination"])
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            moved.append(f"{item['source']} → {item['destination']}")
        preview_store.consume(
            context.internal_id,
            str(params["preview_id"]),
            expected_hash=str(params["preview_hash"]),
        )
        return ToolResult(
            "Organized the approved files:\n"
            + "\n".join(f"- {item}" for item in moved),
            {"root": str(root), "moves": moved},
        )


class DraftNoteTool:
    name = "draft_note"
    read_only = False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from services.action_previews import preview_store

        preview = preview_store.load(
            context.internal_id,
            str(params.get("preview_id") or ""),
            expected_hash=str(params.get("preview_hash") or ""),
            expected_action=self.name,
        )
        payload = dict(preview["payload"])
        root = _approved_root(context, payload.get("root"))
        target = policies.validate_path(
            root / str(payload["path"]), root=root, write=True, allow_missing=True
        )
        existed = target.exists()
        before = target.read_text(encoding="utf-8") if existed else ""
        if existed != bool(payload.get("existed")) or hashlib.sha256(
            before.encode()
        ).hexdigest() != str(payload["before_hash"]):
            from agent.tooling.errors import ToolExecutionError

            raise ToolExecutionError(
                "Note hash changed after preview",
                user_message=(
                    "The note changed after the preview, so I wrote nothing. "
                    "Please generate a fresh preview."
                ),
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(str(payload["content"]), encoding="utf-8")
        temporary.replace(target)
        preview_store.consume(
            context.internal_id,
            str(params["preview_id"]),
            expected_hash=str(params["preview_hash"]),
        )
        return ToolResult(
            f"Saved the approved note to `{target.relative_to(root)}`.",
            {"root": str(root), "path": str(target.relative_to(root))},
        )
