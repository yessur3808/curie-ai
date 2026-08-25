"""Typed, confidence-gated intent resolution for Curie's registered tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Callable, Optional


@dataclass
class ToolRequest:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    needs_approval: bool = False
    explanation: str = ""
    confidence: float = 1.0
    source: str = "deterministic"


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    required: tuple[str, ...] = ()
    defaults: tuple[tuple[str, Any], ...] = ()
    validators: tuple[tuple[str, Callable[[Any], bool]], ...] = ()


_NAME = re.compile(r"^[A-Za-z0-9][\w.-]{1,63}$")
_NONEMPTY = lambda value: isinstance(value, str) and bool(value.strip())
_PATH = lambda value: isinstance(value, str) and "\x00" not in value
_BOOL = lambda value: isinstance(value, bool)

TOOL_PARAMETER_SPECS: dict[str, ParameterSpec] = {
    "ram_usage": ParameterSpec(),
    "hardware": ParameterSpec(),
    "weather": ParameterSpec(required=("query",), validators=(("query", _NONEMPTY),)),
    "research": ParameterSpec(required=("query",), validators=(("query", _NONEMPTY),)),
    "inspect_project": ParameterSpec(defaults=(("path", "."),), validators=(("path", _PATH),)),
    "create_directory": ParameterSpec(required=("name",), validators=(("name", lambda value: isinstance(value, str) and bool(_NAME.fullmatch(value))),)),
    "create_python_project": ParameterSpec(required=("name",), validators=(("name", lambda value: isinstance(value, str) and bool(_NAME.fullmatch(value))),)),
    "run_tests": ParameterSpec(defaults=(("path", "."),), validators=(("path", _PATH),)),
    "project_change": ParameterSpec(
        required=("request",), defaults=(("path", "."), ("run_tests", False)),
        validators=(("request", _NONEMPTY), ("path", _PATH), ("run_tests", _BOOL)),
    ),
}

_MUTATING_ACTIONS = {"create_directory", "create_python_project", "project_change"}
_APPROVE = re.compile(r"^/approve\s+action\s+([a-f0-9]{8})$", re.I)
_REJECT = re.compile(r"^/reject\s+action\s+([a-f0-9]{8})$", re.I)
_ACTION_HINT = re.compile(
    r"\b(?:files?|folder|directory|project|repo|repository|tests?|weather|forecast|"
    r"rain|ram|memory|hardware|computer|research|sources?|investigate|look up|"
    r"create|make|run|inspect|analy[sz]e|fix|implement|generate|modify|refactor)\b",
    re.I,
)


def validate_tool_request(action: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Return normalized parameters or a focused validation error."""
    spec = TOOL_PARAMETER_SPECS.get(action)
    if spec is None:
        return None, "That action is not registered."
    normalized = dict(params or {})
    allowed = set(spec.required) | {key for key, _ in spec.defaults} | {
        key for key, _ in spec.validators
    }
    normalized = {key: value for key, value in normalized.items() if key in allowed}
    for key, value in spec.defaults:
        normalized.setdefault(key, value)
    missing = [key for key in spec.required if key not in normalized or normalized[key] in (None, "")]
    if missing:
        label = "project or directory name" if "name" in missing else missing[0].replace("_", " ")
        return None, f"What {label} should I use?"
    for key, validator in spec.validators:
        if key in normalized and not validator(normalized[key]):
            return None, f"The {key.replace('_', ' ')} is not valid. Could you rephrase it?"
    return normalized, None


def _project_name(text: str) -> Optional[str]:
    match = re.search(
        r"(?:project|directory|folder)(?:\s+(?:called|named))?\s+[‘'\"]?([a-zA-Z0-9][\w.-]{1,63})",
        text,
        re.I,
    )
    return match.group(1) if match else None


def classify_request(text: str) -> Optional[ToolRequest]:
    """Recognize explicit, high-confidence requests without model inference."""
    lowered = text.lower().strip()
    approve = _APPROVE.fullmatch(lowered)
    reject = _REJECT.fullmatch(lowered)
    if approve:
        return ToolRequest("approve", {"token": approve.group(1)})
    if reject:
        return ToolRequest("reject", {"token": reject.group(1)})

    candidates: list[ToolRequest] = []
    if re.search(r"\b(?:what|show|check).*(?:using|uses).*(?:ram|memory)\b|\b(?:ram|memory) usage\b", lowered):
        candidates.append(ToolRequest("ram_usage"))
    if re.search(r"\b(?:computer|system|hardware) spec", lowered):
        candidates.append(ToolRequest("hardware"))
    if re.search(r"\b(?:create|start|make|scaffold)\b.*\b(?:python\s+)?project\b", lowered):
        name = _project_name(text)
        if name:
            candidates.append(ToolRequest("create_python_project", {"name": name}))
        else:
            return ToolRequest("clarify", {"message": "What should I name the Python project?"})
    if re.search(r"\b(?:create|make)\b.*\b(?:directory|folder)\b", lowered):
        name = _project_name(text)
        if name:
            candidates.append(ToolRequest("create_directory", {"name": name}))
        else:
            return ToolRequest("clarify", {"message": "What should I name the directory?"})
    if re.search(r"\b(?:fix|implement|add|generate|modify|adjust|refactor)\b.*\b(?:bug|code|endpoint|project|feature|tests?)\b", lowered):
        candidates.append(ToolRequest(
            "project_change",
            {"request": text, "path": ".", "run_tests": bool(re.search(r"\b(?:run|execute)\b.*\btests?\b", lowered))},
            needs_approval=True,
            explanation="generate and apply code changes inside the configured workspace",
        ))
    elif re.search(r"\b(?:look through|inspect|list|show|review)\b.*\b(?:files?|project|repository|repo)\b", lowered):
        candidates.append(ToolRequest("inspect_project", {"path": "."}))
    elif re.search(r"\b(?:run|execute)\b.*\btests?\b", lowered):
        candidates.append(ToolRequest("run_tests", {"path": "."}))
    if not re.search(r"\b(?:weather|storm|rain)\s+(?:metaphor|symbol|imagery)\b", lowered) and re.search(
        r"\b(?:check|show|get|tell me|what(?:'s| is)|how(?:'s| is)|is it|will it|"
        r"do i need|should i).{0,100}\b(?:weather|forecast|rain(?:s|ing|y)?|jacket|coat|umbrella)\b|"
        r"\b(?:weather|forecast).{0,80}\b(?:in|for|at|today|tomorrow|now)\b",
        lowered,
    ):
        candidates.append(ToolRequest("weather", {"query": text}))
    if re.search(
        r"\b(?:research|analy[sz]e|plan)\b.*\b(?:market|trends?|trip|travel|itinerary|sources?|topic)\b|"
        r"\b(?:research|current sources?)\b",
        lowered,
    ):
        candidates.append(ToolRequest("research", {"query": text}))

    unique = {candidate.action: candidate for candidate in candidates}
    if len(unique) == 1:
        request = next(iter(unique.values()))
        normalized, clarification = validate_tool_request(request.action, request.params)
        return ToolRequest("clarify", {"message": clarification}) if clarification else ToolRequest(
            request.action, normalized or {}, request.needs_approval, request.explanation
        )
    if len(unique) > 1:
        actions = " and ".join(action.replace("_", " ") for action in unique)
        return ToolRequest("clarify", {"message": f"I found more than one possible action: {actions}. Which one should I do first?"})
    return None


def _parse_model_decision(raw: str) -> dict[str, Any] | None:
    match = re.search(r"\{[\s\S]*\}", raw or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


async def _ask_intent_model(prompt: str) -> str:
    """Narrow inference seam so intent routing has no eager model dependency."""
    from llm.manager import ask_llm

    return await asyncio.to_thread(
        ask_llm, prompt, role="fast", temperature=0.0, max_tokens=180
    )


async def resolve_request(text: str) -> Optional[ToolRequest]:
    """Use deterministic routing first, then a typed local classifier when warranted."""
    deterministic = classify_request(text)
    if deterministic is not None or not _ACTION_HINT.search(text):
        return deterministic
    if os.getenv("INTENT_LLM_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return None

    read_only = sorted(set(TOOL_PARAMETER_SPECS) - _MUTATING_ACTIONS)
    prompt = (
        "Classify an assistant tool request. Return only JSON with keys action, params, "
        "confidence, and clarification. action must be one of " + json.dumps(read_only) +
        " or null. Do not classify ordinary conversation. Do not authorize file creation "
        "or code modification. confidence must be 0 to 1. params must be an object. If a "
        "required detail is missing, put one short question in clarification.\nUser: " + text
    )
    raw = await _ask_intent_model(prompt)
    payload = _parse_model_decision(raw)
    if not payload:
        return None
    action = payload.get("action")
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if action not in read_only or not 0.0 <= confidence <= 1.0:
        return None
    normalized, validation_question = validate_tool_request(action, payload.get("params", {}))
    clarification = validation_question or payload.get("clarification")
    if confidence < 0.45:
        return None
    if confidence < 0.78 or clarification:
        question = clarification or f"Do you want me to use the {action.replace('_', ' ')} tool?"
        return ToolRequest("clarify", {"message": str(question)}, confidence=confidence, source="model")
    from agent.tooling import get_runtime_registry

    if action not in get_runtime_registry().names():
        return None
    return ToolRequest(action, normalized or {}, confidence=confidence, source="model")
