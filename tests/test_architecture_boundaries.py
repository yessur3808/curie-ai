"""Import-direction gates for the Phase 1 turn architecture."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python_files(relative: str) -> list[Path]:
    path = ROOT / relative
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _violations(paths: list[Path], forbidden: tuple[str, ...]) -> list[str]:
    problems = []
    for path in paths:
        for imported in sorted(_imports(path)):
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in forbidden
            ):
                problems.append(f"{path.relative_to(ROOT)} -> {imported}")
    return problems


def test_connectors_do_not_import_provider_specific_memory_repositories():
    violations = _violations(
        _python_files("connectors"),
        (
            "memory.database",
            "memory.local_store",
            "memory.repositories",
            "memory.schema_migrations",
        ),
    )
    assert violations == []


def test_response_modules_do_not_import_capability_implementations():
    response_modules = [
        ROOT / "agent" / "response_planner.py",
        ROOT / "agent" / "orchestration" / "response_policy.py",
    ]
    violations = _violations(
        response_modules,
        ("agent.tooling", "connectors", "services"),
    )
    assert violations == []


def test_memory_modules_do_not_import_chat_workflow():
    violations = _violations(
        _python_files("memory"),
        ("agent.chat_workflow",),
    )
    assert violations == []


def test_capability_implementations_do_not_import_telegram_types():
    violations = _violations(
        _python_files("agent/tooling"),
        ("telegram", "connectors.telegram"),
    )
    assert violations == []


def test_production_modules_do_not_import_evaluation_code():
    production_paths: list[Path] = []
    for relative in (
        "agent",
        "cli",
        "connectors",
        "contracts",
        "memory",
        "services",
        "utils",
    ):
        production_paths.extend(_python_files(relative))
    violations = _violations(production_paths, ("evaluation",))
    assert violations == []


def test_turn_kernel_does_not_depend_on_connectors():
    violations = _violations(_python_files("agent/kernel"), ("connectors",))
    assert violations == []
