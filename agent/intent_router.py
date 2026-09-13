"""Typed, confidence-gated intent resolution for Curie's registered tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Callable, Iterable, Mapping, Optional


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
_POWER_STATE = lambda value: isinstance(value, str) and value in {"on", "off"}

TOOL_PARAMETER_SPECS: dict[str, ParameterSpec] = {
    "ram_usage": ParameterSpec(),
    "hardware": ParameterSpec(),
    "network_speed": ParameterSpec(),
    "gmail_search": ParameterSpec(required=("query",), validators=(("query", _NONEMPTY),)),
    "gmail_read": ParameterSpec(required=("message_id",), validators=(("message_id", _NONEMPTY),)),
    "gmail_send": ParameterSpec(required=("recipient", "subject", "body"), validators=(("recipient", _NONEMPTY), ("subject", _NONEMPTY), ("body", _NONEMPTY))),
    "x_search": ParameterSpec(required=("query",), validators=(("query", _NONEMPTY),)),
    "x_read": ParameterSpec(required=("post_id",), validators=(("post_id", lambda value: isinstance(value, str) and value.isdigit()),)),
    "x_post": ParameterSpec(required=("text",), validators=(("text", _NONEMPTY),)),
    "x_reply": ParameterSpec(required=("post_id", "text"), validators=(("post_id", lambda value: isinstance(value, str) and value.isdigit()), ("text", _NONEMPTY))),
    "x_dm_read": ParameterSpec(),
    "x_dm_send": ParameterSpec(required=("participant_id", "text"), validators=(("participant_id", lambda value: isinstance(value, str) and value.isdigit()), ("text", _NONEMPTY))),
    "browser_open": ParameterSpec(required=("url",), validators=(("url", lambda value: isinstance(value, str) and value.startswith(("http://", "https://"))),)),
    "browser_snapshot": ParameterSpec(),
    "browser_click": ParameterSpec(required=("text",), validators=(("text", _NONEMPTY),)),
    "browser_fill": ParameterSpec(required=("label", "value"), validators=(("label", _NONEMPTY), ("value", _NONEMPTY))),
    "browser_close": ParameterSpec(),
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
    "home_status": ParameterSpec(
        defaults=(("target", ""), ("provider", "")),
        validators=(("target", lambda value: isinstance(value, str)), ("provider", lambda value: isinstance(value, str))),
    ),
    "home_control": ParameterSpec(
        required=("target", "state"), defaults=(("provider", ""),),
        validators=(("target", _NONEMPTY), ("state", _POWER_STATE), ("provider", lambda value: isinstance(value, str))),
    ),
}

_MUTATING_ACTIONS = {"create_directory", "create_python_project", "project_change", "gmail_send", "x_post", "x_reply", "x_dm_send", "browser_click", "browser_fill", "home_control"}
_APPROVE = re.compile(r"^/approve\s+action\s+([a-f0-9]{8})$", re.I)
_REJECT = re.compile(r"^/reject\s+action\s+([a-f0-9]{8})$", re.I)
_ACTION_HINT = re.compile(
    r"\b(?:files?|folder|directory|project|repo|repository|tests?|weather|forecast|"
    r"rain|ram|memory|hardware|computer|internet|network|speed|latency|ping|email|gmail|twitter|tweet|\bx\b|direct message|research|sources?|investigate|look up|"
    r"home|house|device|light|lamp|plug|switch|air conditioner|ac|tapo|nanoleaf|smartthings|petlibro|govee|mi home|xiaomi|thinq|"
    r"create|make|run|inspect|analy[sz]e|fix|implement|generate|modify|refactor|turn on|turn off)\b",
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


def _home_target(value: str) -> str:
    target = re.sub(r"^[\s,]*(?:the\s+)?", "", value.strip(), flags=re.I)
    target = re.sub(r"[\s,]*(?:please|now|for me|at home|in the house)[.!?\s]*$", "", target, flags=re.I)
    return target.strip(" \t.,!?")


def _home_control_request(command: str) -> ToolRequest | None:
    polite = r"(?:please\s+|could you\s+|can you\s+|would you\s+)?"
    patterns = (
        rf"^{polite}(?:turn|switch|power)\s+(?P<state>on|off)\s+(?P<target>.+)$",
        rf"^{polite}(?:turn|switch|power)\s+(?P<target>.+?)\s+(?P<state>on|off)(?:\s+please)?[.!?]?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, command.strip(), re.I | re.S)
        if not match:
            continue
        target = _home_target(match.group("target"))
        if _normalize_pronoun(target):
            return ToolRequest("clarify", {"message": "Which home device should I control? Please use its name."})
        params, error = validate_tool_request(
            "home_control", {"target": target, "state": match.group("state").casefold()}
        )
        return ToolRequest("clarify", {"message": error}) if error else ToolRequest(
            "home_control", params or {}, explanation=f"turn {target} {match.group('state').casefold()}"
        )
    return None


def _normalize_pronoun(target: str) -> bool:
    return not target or target.casefold() in {"it", "that", "that one", "this", "this one", "device", "the device"}


def _recent_explicit_home_target(history: Iterable[Any] | None) -> str | None:
    """Recover a device named by the user in the immediately preceding turns."""
    for item in reversed(list(history or [])[-8:]):
        if isinstance(item, Mapping):
            role, content = item.get("role"), item.get("content")
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role, content = item[0], item[1]
        else:
            continue
        if str(role).casefold() != "user" or not isinstance(content, str):
            continue
        previous = classify_request(content)
        if previous and previous.action in {"home_status", "home_control"}:
            target = _home_target(str(previous.params.get("target") or ""))
            if target and not _normalize_pronoun(target):
                return target
    return None


def classify_request(
    text: str, history: Iterable[Any] | None = None
) -> Optional[ToolRequest]:
    """Recognize explicit, high-confidence requests without model inference."""
    lowered = text.lower().strip()
    approve = _APPROVE.fullmatch(lowered)
    reject = _REJECT.fullmatch(lowered)
    if approve:
        return ToolRequest("approve", {"token": approve.group(1)})
    if reject:
        return ToolRequest("reject", {"token": reject.group(1)})

    # Explicit account commands keep external writes unambiguous and previewable.
    command = text.strip()
    match = re.fullmatch(r"/home\s+(?:status|summary)(?:\s+(.+))?", command, re.I | re.S)
    if match:
        return ToolRequest("home_status", {"target": _home_target(match.group(1) or ""), "provider": ""})
    match = re.fullmatch(r"/home\s+(on|off)\s+(.+)", command, re.I | re.S)
    if match:
        target = _home_target(match.group(2))
        if _normalize_pronoun(target):
            return ToolRequest("clarify", {"message": "Which home device should I control? Please use its name."})
        return ToolRequest("home_control", {"target": target, "state": match.group(1).casefold(), "provider": ""}, explanation=f"turn {target} {match.group(1).casefold()}")
    home_control = _home_control_request(command)
    if home_control is not None:
        if home_control.action == "clarify":
            target = _recent_explicit_home_target(history)
            state_match = re.search(r"\b(on|off)\b", command, re.I)
            if target and state_match:
                state = state_match.group(1).casefold()
                return ToolRequest(
                    "home_control",
                    {"target": target, "state": state, "provider": ""},
                    explanation=f"turn {target} {state}",
                )
        return home_control
    if re.search(
        r"\b(?:home|house|smart[- ]?home)\s+(?:status|summary|devices?)\b|"
        r"\b(?:what(?:'s| is)|which devices? (?:are|is)|show|list|summari[sz]e)\b.{0,60}\b(?:running|on|off|online|offline)\b.{0,30}\b(?:at home|in (?:my|the) (?:home|house))\b|"
        r"\b(?:what(?:'s| is)|which devices? (?:are|is))\b.{0,30}\b(?:running|on|off|online|offline)\b.{0,20}\b(?:home|house)\b",
        command,
        re.I,
    ):
        return ToolRequest("home_status", {"target": "", "provider": ""})
    match = re.fullmatch(
        r"(?:please\s+)?(?:check|show|get|what(?:'s| is))\s+(?:the\s+)?(?:status\s+(?:of|for)\s+)?(.+?)(?:\s+status)?[.!?]?",
        command,
        re.I | re.S,
    )
    if match and re.search(r"\b(?:device|light|lamp|plug|switch|fan|air|ac|tapo|nanoleaf|smartthings|petlibro|govee|mi home|xiaomi|thinq)\b", match.group(1), re.I):
        return ToolRequest("home_status", {"target": _home_target(match.group(1)), "provider": ""})
    match = re.fullmatch(r"(?:is|are)\s+(?:the\s+|my\s+)?(.+?)\s+(?:on|off|running|online|offline)[?!.]?", command, re.I | re.S)
    if match and re.search(r"\b(?:device|light|lamp|plug|switch|fan|air|ac|tapo|nanoleaf|smartthings|petlibro|govee|mi home|xiaomi|thinq|bedroom|living room|kitchen|garage)\b", match.group(1), re.I):
        return ToolRequest("home_status", {"target": _home_target(match.group(1)), "provider": ""})
    match = re.fullmatch(r"/browser\s+open\s+(https?://\S+)", command, re.I)
    if match:
        return ToolRequest("browser_open", {"url": match.group(1)})
    if re.fullmatch(r"/browser\s+snapshot", command, re.I):
        return ToolRequest("browser_snapshot")
    match = re.fullmatch(r"/browser\s+click\s+(.+)", command, re.I | re.S)
    if match:
        label = match.group(1).strip()
        return ToolRequest("browser_click", {"text": label}, needs_approval=True, explanation=f"click the browser element labelled {label!r}")
    match = re.fullmatch(r"/browser\s+fill\s+([^|]+)\|(.+)", command, re.I | re.S)
    if match:
        label, value = (part.strip() for part in match.groups())
        return ToolRequest("browser_fill", {"label": label, "value": value}, needs_approval=True, explanation=f"fill the browser field labelled {label!r}")
    if re.fullmatch(r"/browser\s+close", command, re.I):
        return ToolRequest("browser_close")
    match = re.fullmatch(r"/gmail\s+search\s+(.+)", command, re.I | re.S)
    if match:
        return ToolRequest("gmail_search", {"query": match.group(1).strip()})
    match = re.fullmatch(r"/gmail\s+read\s+(\S+)", command, re.I)
    if match:
        return ToolRequest("gmail_read", {"message_id": match.group(1)})
    match = re.fullmatch(r"/gmail\s+send\s+([^|]+)\|([^|]+)\|(.+)", command, re.I | re.S)
    if match:
        recipient, subject, body = (part.strip() for part in match.groups())
        return ToolRequest("gmail_send", {"recipient": recipient, "subject": subject, "body": body}, needs_approval=True, explanation=f"send this email to {recipient}")
    match = re.fullmatch(r"/(?:x|twitter)\s+search\s+(.+)", command, re.I | re.S)
    if match:
        return ToolRequest("x_search", {"query": match.group(1).strip()})
    match = re.fullmatch(r"/(?:x|twitter)\s+read\s+(\d+)", command, re.I)
    if match:
        return ToolRequest("x_read", {"post_id": match.group(1)})
    match = re.fullmatch(r"/(?:x|twitter)\s+post\s+(.+)", command, re.I | re.S)
    if match:
        return ToolRequest("x_post", {"text": match.group(1).strip()}, needs_approval=True, explanation="publish this post on X")
    match = re.fullmatch(r"/(?:x|twitter)\s+reply\s+(\d+)\s*\|\s*(.+)", command, re.I | re.S)
    if match:
        return ToolRequest("x_reply", {"post_id": match.group(1), "text": match.group(2).strip()}, needs_approval=True, explanation=f"publish this reply to X post {match.group(1)}")
    if re.fullmatch(r"/(?:x|twitter)\s+dm\s+(?:read|list)", command, re.I):
        return ToolRequest("x_dm_read")
    match = re.fullmatch(r"/(?:x|twitter)\s+dm\s+send\s+(\d+)\s*\|\s*(.+)", command, re.I | re.S)
    if match:
        return ToolRequest("x_dm_send", {"participant_id": match.group(1), "text": match.group(2).strip()}, needs_approval=True, explanation=f"send this X direct message to user ID {match.group(1)}")

    candidates: list[ToolRequest] = []
    if re.search(r"\b(?:what|show|check).*(?:using|uses).*(?:ram|memory)\b|\b(?:ram|memory) usage\b", lowered):
        candidates.append(ToolRequest("ram_usage"))
    if re.search(r"\b(?:computer|system|hardware) spec", lowered):
        candidates.append(ToolRequest("hardware"))
    if re.search(
        r"\b(?:internet|network|connection|wi-?fi)\b.{0,50}\b(?:speed|latency|ping)\b|"
        r"\b(?:speed\s*test|test|measure|check)\b.{0,50}\b(?:download|upload|latency|ping|network speed)\b",
        lowered,
    ):
        candidates.append(ToolRequest("network_speed"))
    if re.search(r"\b(?:search|find|look for)\b.{0,30}\b(?:gmail|emails?|inbox)\b|\b(?:gmail|inbox)\b.{0,30}\b(?:search|find)\b", lowered):
        candidates.append(ToolRequest("gmail_search", {"query": text}))
    if re.search(r"\b(?:search|find)\b.{0,30}\b(?:x|twitter|tweets?|posts?)\b|\b(?:x|twitter)\b.{0,30}\b(?:search|find)\b", lowered):
        candidates.append(ToolRequest("x_search", {"query": text}))
    if re.search(r"\b(?:read|show|check)\b.{0,30}\b(?:x|twitter)\s+(?:dms?|direct messages?)\b", lowered):
        candidates.append(ToolRequest("x_dm_read"))
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
        r"do i need|should i|will i need).{0,100}\b(?:weather|forecast|rain(?:s|ing|y)?|humid(?:ity)?|jacket|coat|umbrella)\b|"
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
