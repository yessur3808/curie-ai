"""Versioned, owner-scoped lifecycle for safely learned abilities."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import PurePath
import re
import time
from typing import Any, Mapping
from urllib.parse import urlsplit

from agent.tooling import ToolContext, get_runtime_registry

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_FORBIDDEN_CONTENT = re.compile(
    r"```|\b(?:python|javascript|shell|bash|powershell|sql)\b|\$\(|<%|{{.*}}|"
    r"\b(?:eval|exec|subprocess|os\.system)\s*\(",
    re.I | re.S,
)
_SKILL_COMMAND = re.compile(
    r"^/?skill\s+(inspect|disable|rollback|export|delete|feedback|archive)"
    r"(?:\s+([a-z0-9_-]{2,64}))?(?:\s+(.*))?$",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    # Lazy access preserves the existing test seam and avoids import cycles.
    from memory import adaptive

    return adaptive.mongo_db


def _list(owner_id: str, status: str | None = None) -> list[dict]:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_abilities

        return list_abilities(owner_id, status)
    query: dict[str, Any] = {"$or": [{"owner_id": owner_id}, {"internal_id": owner_id}]}
    if status:
        query["status"] = status
    return list(_db().learned_abilities.find(query).limit(200))


def _save(document: dict) -> None:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import upsert_ability

        upsert_ability(document)
        return
    _db().learned_abilities.update_one(
        {"_id": document["_id"]}, {"$set": document}, upsert=True
    )


def _update(owner_id: str, skill_id: str, updates: Mapping[str, Any]) -> bool:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import update_ability

        return update_ability(owner_id, skill_id, dict(updates))
    result = _db().learned_abilities.update_one(
        {"_id": skill_id, "$or": [{"owner_id": owner_id}, {"internal_id": owner_id}]},
        {"$set": dict(updates)},
    )
    return bool(getattr(result, "modified_count", 0))


def _delete(owner_id: str, name: str) -> int:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import delete_abilities

        return delete_abilities(owner_id, name)
    result = _db().learned_abilities.delete_many({"owner_id": owner_id, "name": name})
    return int(getattr(result, "deleted_count", 0))


def _validate_schema(schema: Mapping[str, Any]) -> None:
    if schema.get("type", "object") != "object":
        raise ValueError("Learned skill input_schema must describe an object")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError("Learned skill schema properties must be an object")
    allowed = {"string", "number", "integer", "boolean", "array"}
    if any(
        not isinstance(spec, Mapping) or spec.get("type") not in allowed
        for spec in properties.values()
    ):
        raise ValueError(
            "Learned skill schemas may contain only bounded JSON value types"
        )


def _validate_literal(value: Any) -> None:
    if isinstance(value, str):
        if _FORBIDDEN_CONTENT.search(value):
            raise ValueError(
                "Generated code and unrestricted templates are not allowed"
            )
        parsed = urlsplit(value)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            raise ValueError("Only HTTP(S) URLs are allowed in workflow parameters")
        if parsed.hostname and parsed.hostname.casefold() in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Private or loopback URLs are not allowed")
        path = PurePath(value)
        if (path.is_absolute() or ".." in path.parts) and not parsed.scheme:
            raise ValueError(
                "Workflow paths must remain relative and cannot traverse parents"
            )
    elif isinstance(value, Mapping):
        for nested in value.values():
            _validate_literal(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_literal(nested)
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError("Workflow parameters must be JSON-compatible")


def validate_skill(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate structure, capabilities, permissions, paths, URLs, and templates."""
    name = str(document.get("name", ""))
    if not _NAME.fullmatch(name):
        raise ValueError("Skill name must be lowercase snake_case")
    _validate_schema(document.get("input_schema", {"type": "object", "properties": {}}))
    kind = document.get("kind")
    if kind not in {"declarative_response", "executable_workflow"}:
        raise ValueError(
            "Skill kind must be declarative_response or executable_workflow"
        )
    procedure = str(document.get("procedure", ""))
    if _FORBIDDEN_CONTENT.search(procedure):
        raise ValueError(
            "Generated code, SQL, shell, and unrestricted templates are forbidden"
        )
    steps = document.get("workflow_steps", [])
    allowed_tools = tuple(dict.fromkeys(document.get("allowed_tools", [])))
    registry = get_runtime_registry()
    required_permissions: set[str] = set()
    if kind == "declarative_response" and steps:
        raise ValueError("Declarative response recipes cannot execute workflow steps")
    for step in steps:
        if not isinstance(step, Mapping) or set(step) - {"tool", "params"}:
            raise ValueError("Each workflow step may contain only tool and params")
        tool = str(step.get("tool", ""))
        if tool not in allowed_tools:
            raise ValueError(f"Workflow tool {tool!r} is not explicitly allowed")
        capability = registry.get(tool)
        required_permissions.update(capability.required_permissions)
        _validate_literal(step.get("params", {}))
    if set(allowed_tools) != {str(step.get("tool")) for step in steps}:
        raise ValueError(
            "allowed_tools must exactly match tools used by workflow_steps"
        )
    declared = set(document.get("required_permissions", []))
    if declared != required_permissions:
        raise ValueError(
            "required_permissions must exactly match registered tool policies"
        )
    triggers = [
        str(item).strip().casefold() for item in document.get("trigger_examples", [])
    ]
    if not triggers or any(not item or len(item) > 120 for item in triggers):
        raise ValueError("At least one bounded trigger example is required")
    return tuple(sorted(required_permissions))


def evaluate_skill(document: Mapping[str, Any]) -> dict[str, Any]:
    """Run deterministic representative and adversarial validation without execution."""
    cases: dict[str, bool] = {}
    try:
        validate_skill(document)
        cases["representative_trigger"] = True
    except (KeyError, ValueError):
        cases["representative_trigger"] = False
    owner = str(document.get("owner_id", ""))
    cases["owner_scope"] = bool(owner) and str(document.get("_id", "")).startswith(
        f"{owner}:"
    )
    adversarial = {
        "unlisted_tool": {
            **document,
            "kind": "executable_workflow",
            "workflow_steps": [{"tool": "inspect_project", "params": {}}],
            "allowed_tools": [],
        },
        "code_injection": {**document, "procedure": "```python\nexec(payload)\n```"},
        "path_traversal": {
            **document,
            "kind": "executable_workflow",
            "workflow_steps": [
                {"tool": "inspect_project", "params": {"path": "../secret"}}
            ],
            "allowed_tools": ["inspect_project"],
            "required_permissions": [],
        },
    }
    for case_id, candidate in adversarial.items():
        try:
            validate_skill(candidate)
        except (KeyError, ValueError):
            cases[case_id] = True
        else:
            cases[case_id] = False
    failures = [case_id for case_id, passed in cases.items() if not passed]
    score = round(sum(cases.values()) / len(cases), 2)
    return {
        "score": score,
        "cases": cases,
        "failures": failures,
        "passed": not failures,
    }


def draft_skill(
    owner_id: str,
    *,
    name: str,
    trigger_examples: list[str],
    procedure: str = "",
    input_schema: Mapping[str, Any] | None = None,
    workflow_steps: list[dict] | None = None,
    allowed_tools: list[str] | None = None,
    required_permissions: list[str] | None = None,
    kind: str = "declarative_response",
) -> dict:
    """Draft and validate a new immutable version without executing it."""
    existing = [item for item in _list(str(owner_id)) if item.get("name") == name]
    normalized_triggers = [item.strip().casefold() for item in trigger_examples]
    for item in existing:
        if item.get("status") == "active" and set(normalized_triggers) & {
            str(trigger).casefold()
            for trigger in item.get("trigger_examples", [item.get("trigger", "")])
        }:
            if item.get("procedure") == procedure and item.get(
                "workflow_steps", []
            ) == (workflow_steps or []):
                return item
            raise ValueError("An active skill already owns an identical trigger")
    version = max((int(item.get("version", 1)) for item in existing), default=0) + 1
    skill_id = f"{owner_id}:{name}:v{version}"
    document = {
        "_id": skill_id,
        "id": skill_id,
        "owner_id": str(owner_id),
        "internal_id": str(owner_id),
        "name": name,
        "version": version,
        "trigger_examples": trigger_examples,
        "trigger": trigger_examples[0],
        "input_schema": dict(input_schema or {"type": "object", "properties": {}}),
        "workflow_steps": list(workflow_steps or []),
        "allowed_tools": list(allowed_tools or []),
        "required_permissions": list(required_permissions or []),
        "procedure": procedure,
        "kind": kind,
        "status": "pending",
        "evaluation_score": 0.0,
        "requires_confirmation": True,
        "usage_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "feedback": [],
        "created_at": _now(),
    }
    validate_skill(document)
    _save(document)
    return document


def approve_skill(owner_id: str, name: str) -> dict | None:
    pending = [item for item in _list(owner_id, "pending") if item.get("name") == name]
    if not pending:
        return None
    document = max(pending, key=lambda item: int(item.get("version", 1)))
    evaluation = evaluate_skill(document)
    status = (
        "active"
        if evaluation["passed"] and evaluation["score"] >= 0.8
        else "evaluation_failed"
    )
    _update(
        owner_id,
        str(document.get("id") or document.get("_id")),
        {
            "status": status,
            "evaluation_score": evaluation["score"],
            "evaluation": evaluation,
            "reviewed_at": _now(),
        },
    )
    return {
        **document,
        "status": status,
        "evaluation_score": evaluation["score"],
        "evaluation": evaluation,
    }


def reject_skill(owner_id: str, name: str) -> bool:
    pending = [item for item in _list(owner_id, "pending") if item.get("name") == name]
    return bool(pending) and _update(
        owner_id,
        str(pending[-1].get("id") or pending[-1].get("_id")),
        {"status": "rejected", "reviewed_at": _now()},
    )


def matching_skills(owner_id: str, text: str, limit: int = 3) -> list[dict]:
    lowered = text.casefold()
    matches = []
    for item in _list(owner_id, "active"):
        triggers = item.get("trigger_examples") or [item.get("trigger", "")]
        if any(str(trigger).casefold() in lowered for trigger in triggers):
            matches.append(item)
    return matches[:limit]


async def invoke_skill(
    skill: Mapping[str, Any], params: Mapping[str, Any], context: ToolContext
) -> list[Any]:
    """Invoke a typed workflow with fresh registry policy checks for every step."""
    if str(skill.get("owner_id") or skill.get("internal_id")) != context.internal_id:
        raise PermissionError("Learned skills are available only to their owner")
    validate_skill(skill)
    if skill.get("status") != "active":
        raise PermissionError("Learned skill is not active")
    started = time.perf_counter()
    results, outcome, last_tool = [], "completed", ""
    try:
        for step in skill.get("workflow_steps", []):
            last_tool = str(step["tool"])
            step_params = dict(step.get("params", {}))
            step_params.update(
                {
                    key: value
                    for key, value in params.items()
                    if key in skill.get("input_schema", {}).get("properties", {})
                }
            )
            results.append(
                await get_runtime_registry().execute(step["tool"], step_params, context)
            )
        return results
    except Exception:
        outcome = "failed"
        raise
    finally:
        try:
            from memory.adaptation import record_operational_signal

            record_operational_signal(
                context.internal_id,
                "accepted_action" if outcome == "completed" else "tool_failure",
                tool=last_tool,
            )
        except Exception:
            pass
        skill_id = str(skill.get("id") or skill.get("_id"))
        current = next(
            (
                item
                for item in _list(context.internal_id)
                if str(item.get("id") or item.get("_id")) == skill_id
            ),
            dict(skill),
        )
        updates = {
            "usage_count": int(current.get("usage_count", 0)) + 1,
            "success_count": int(current.get("success_count", 0))
            + int(outcome == "completed"),
            "failure_count": int(current.get("failure_count", 0))
            + int(outcome == "failed"),
            "last_result": outcome,
            "last_latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "last_used_at": _now(),
        }
        _update(context.internal_id, skill_id, updates)


def handle_skill_command(owner_id: str, text: str) -> str | None:
    match = _SKILL_COMMAND.fullmatch(text.strip())
    if not match:
        return None
    action, name, argument = (
        match.group(1).casefold(),
        (match.group(2) or "").casefold(),
        (match.group(3) or "").strip(),
    )
    skills = [item for item in _list(owner_id) if not name or item.get("name") == name]
    if action == "export":
        return json.dumps(
            {"owner_id": owner_id, "skills": skills}, default=str, indent=2
        )
    if action == "inspect":
        return (
            json.dumps(skills, default=str, indent=2)
            if skills
            else "No learned skills found."
        )
    if not name:
        return f"Use `/skill {action} <name>`."
    if action == "delete":
        return f"Deleted {_delete(owner_id, name)} version(s) of `{name}`."
    if not skills:
        return f"I could not find a learned skill named `{name}`."
    latest = max(skills, key=lambda item: int(item.get("version", 1)))
    if action == "disable":
        _update(
            owner_id, str(latest.get("id") or latest.get("_id")), {"status": "disabled"}
        )
        return f"Disabled learned skill `{name}`."
    if action == "archive":
        if (
            int(latest.get("usage_count", 0)) > 0
            and int(latest.get("failure_count", 0)) < 3
        ):
            return (
                f"`{name}` is still in use and has not failed consistently; "
                "it was not archived."
            )
        _update(
            owner_id,
            str(latest.get("id") or latest.get("_id")),
            {"status": "archived", "archived_at": _now()},
        )
        return f"Archived learned skill `{name}` after your confirmation."
    if action == "rollback":
        active = [item for item in skills if item.get("status") == "active"]
        previous = sorted(
            skills, key=lambda item: int(item.get("version", 1)), reverse=True
        )[1:2]
        if not previous:
            return f"No earlier version of `{name}` is available."
        for item in active:
            _update(
                owner_id,
                str(item.get("id") or item.get("_id")),
                {"status": "rolled_back"},
            )
        target = previous[0]
        _update(
            owner_id, str(target.get("id") or target.get("_id")), {"status": "active"}
        )
        return f"Rolled `{name}` back to version {target.get('version', 1)}."
    if action == "feedback":
        feedback = list(latest.get("feedback", []))
        feedback.append({"value": argument[:200], "created_at": _now()})
        _update(
            owner_id,
            str(latest.get("id") or latest.get("_id")),
            {"feedback": feedback[-50:]},
        )
        return f"Recorded feedback for `{name}`."
    return None
