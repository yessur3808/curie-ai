"""Canonical machine-readable contracts and truthful capability discovery."""

from __future__ import annotations

import re
from typing import Any

CONTRACT_VERSION = "1.1.0"


def contract_catalog() -> dict[str, Any]:
    from agent.kernel.pipeline import pipeline_contract
    from services.media_ingestion import media_conformance_contract

    return {
        "contract_version": CONTRACT_VERSION,
        "turn_pipeline": pipeline_contract(),
        "connector": {
            "version": "1.0",
            "required_inbound": (
                "platform",
                "external_user_id",
                "external_chat_id",
                "message_id",
                "text",
                "timestamp",
                "internal_id",
            ),
            "attachment_kinds": ("image", "document", "audio"),
            "delivery": {"bounded": True, "text_fallback": True},
        },
        "tool": {
            "version": "1.0",
            "required_definition_fields": (
                "name",
                "version",
                "input_schema",
                "output_schema",
                "risk",
                "approval_policy",
                "resource_policy",
            ),
            "risk_values": ("read_only", "mutating"),
        },
        "media": {"version": "1.0", "connectors": media_conformance_contract()},
        "memory": {
            "version": "1.0",
            "owner_scoped": True,
            "required_metadata": ("provenance", "confidence", "created_at"),
            "controls": ("inspect", "correct", "delete", "export", "rollback"),
        },
        "voice": {
            "version": "1.0",
            "modes": ("text", "voice"),
            "profiles": ("clear", "soft", "expressive", "french", "custom"),
            "required_fallback": "complete_text",
        },
        "response_policy": {
            "version": "1.0",
            "required_dimensions": (
                "directness",
                "acknowledgement",
                "next_action",
                "length",
                "affection",
                "french_usage",
            ),
            "urgent_suppresses_decoration": True,
        },
    }


def discover_capabilities(*, include_unavailable: bool = False) -> dict[str, Any]:
    """Describe only executable, healthy features unless diagnostics are requested."""
    from agent.tooling import get_runtime_registry
    from services.runtime_health import capability_health

    registry = get_runtime_registry()
    definitions = registry.all() if include_unavailable else registry.available_tools()
    items = []
    for definition in definitions:
        available, reason = definition.availability()
        if available or include_unavailable:
            item = definition.as_dict()
            # Routing regexes, module paths, and dependency secrets are internal.
            item.pop("input_schema", None)
            item.pop("output_schema", None)
            if available:
                item["error"] = None
            else:
                item["error"] = reason
            items.append(item)
    readiness = capability_health()
    return {
        "schema_version": "1.0",
        "contract_version": CONTRACT_VERSION,
        "status": readiness["status"],
        "capabilities": items,
        "runtime": readiness["capabilities"],
    }


_GENERAL_CAPABILITY_QUESTION = re.compile(
    r"^(?:what (?:can|are) you (?:do|capable of)|"
    r"what (?:useful )?(?:things )?can you help me with|"
    r"what are your (?:current )?capabilities|"
    r"what (?:tools|skills|abilities) (?:do you have|are available|are active)|"
    r"(?:show|list) (?:me )?(?:your )?(?:current )?(?:capabilities|tools|skills)|"
    r"are (?:all )?(?:of )?your (?:tools|skills|capabilities) (?:still )?(?:active|available|working))"
    r"[?.! ]*$",
    re.I,
)
_CAPABILITY_FORMAT_SUFFIX = re.compile(r"\s+(?:give|show|format|keep)\b.{0,120}$", re.I)
_FOCUSED_CAPABILITY_QUESTION = re.compile(
    r"^(?:(?:can|could|do) you (?:currently |still |directly )?"
    r"(?:access|use|read|check|control|track|find|send|reply (?:with|using))|"
    r"do you (?:currently |still )?have access to)\s+"
    r"(?P<subject>(?:the |my )?(?:dashboard(?: info(?:rmation)?)?|google calendar|calendar|"
    r"devices?|lights?|smart home|flights?|maps?|places?|voice messages?|audio messages?))"
    r"(?:\s+directly)?[?.! ]*$",
    re.I,
)


def _capability_item(report: dict[str, Any], *names: str) -> dict[str, Any] | None:
    wanted = {name.casefold() for name in names}
    for item in report["capabilities"]:
        if str(item.get("name", "")).casefold() in wanted:
            return item
    return None


def _natural_capability_summary(report: dict[str, Any]) -> str:
    items = report["capabilities"]
    active = {str(item.get("name")) for item in items if item.get("available")}
    unavailable = [
        str(item.get("display_name")) for item in items if not item.get("available")
    ]
    lines = [
        f"**Current capabilities: {len(active)} active**",
        "",
        "- **Conversation and memory:** contextual chat, long-term memory, preferences, and planning",
    ]
    groups = (
        (
            {"home_control", "home_status", "home_alias"},
            "**Smart home:** find, correlate, control, and verify devices and lights",
        ),
        (
            {"research", "weather", "flight_track", "places_search"},
            "**Live information:** web research, news, weather forecasts, flights, maps, and places",
        ),
        (
            {"calendar_list", "calendar_create", "gmail_search", "gmail_send"},
            "**Google services:** read or schedule Calendar events and work with Gmail",
        ),
        (
            {"dashboard_snapshot", "hardware", "network_speed"},
            "**Operations:** read the Curie dashboard, health, hardware, network, tasks, and diagnostics",
        ),
        (
            {"x_read", "x_search", "x_post", "x_reply"},
            "**X:** read, search, post, and reply under the configured approval policy",
        ),
        (
            {"inspect_project", "project_change", "run_tests"},
            "**Development:** inspect projects, make approved code changes, and run tests",
        ),
    )
    lines.extend(f"- {label}" for names, label in groups if active & names)
    speech = report["runtime"].get("speech", {})
    if speech.get("ready"):
        lines.append("- **Voice:** send local trained-voice replies on Telegram")
    if unavailable:
        lines.extend(
            (
                "",
                "**Not ready:** " + ", ".join(unavailable[:5]),
            )
        )
    lines.append("")
    lines.append("Ask about any one area and I’ll give you the exact limits.")
    return "\n".join(lines)


def _focused_capability_answer(subject: str, report: dict[str, Any]) -> str:
    lowered = subject.casefold()
    if "voice" in lowered or "audio" in lowered:
        speech = report["runtime"].get("speech", {})
        if speech.get("ready"):
            backend = speech.get("backend") or "local speech"
            return (
                "Yes. I can send Telegram voice messages when you ask for a voice "
                f"reply. The active backend is **{backend}**."
            )
        return "Not right now. Voice replies are configured, but speech synthesis is currently unavailable; I’ll reply in text instead."

    lookup = {
        "dashboard": ("dashboard_snapshot",),
        "calendar": ("calendar_list", "calendar_create"),
        "device": ("home_status", "home_control"),
        "light": ("home_status", "home_control"),
        "smart home": ("home_status", "home_control"),
        "flight": ("flight_tracker",),
        "map": ("navigation_skill", "places_search"),
        "place": ("places_search",),
    }
    names: tuple[str, ...] = ()
    label = subject.strip().rstrip("?.!")
    for keyword, candidates in lookup.items():
        if keyword in lowered:
            names = candidates
            break
    items = [item for name in names if (item := _capability_item(report, name))]
    available = [item for item in items if item.get("available")]
    if available:
        if "dashboard" in lowered:
            return "Yes. I can read live health, model, connector, task, resource, and error data from Curie’s dashboard."
        if "calendar" in lowered:
            return "Yes. I can read your connected Google Calendar and create events after a clear preview and approval."
        if any(word in lowered for word in ("device", "light", "smart home")):
            return "Yes. I can match natural device names, read their live state, control them, and verify the result."
        if "flight" in lowered:
            return "Yes. I can track flight schedules, delays, terminals, gates, and live position when the provider has it."
        if any(word in lowered for word in ("map", "place")):
            return "Yes. I can provide directions, Google Maps links, and nearby place suggestions."
        descriptions = "; ".join(str(item["description"]) for item in available)
        return f"Yes. {descriptions}"
    reasons = [str(item.get("error")) for item in items if item.get("error")]
    if reasons:
        return f"Not yet. {reasons[0]}"
    return f"I do not currently have a verified, active capability for {label}."


def handle_capabilities_command(text: str) -> str | None:
    cleaned = text.strip()
    command = cleaned.casefold()
    query = _CAPABILITY_FORMAT_SUFFIX.sub("", cleaned).rstrip()
    focused = _FOCUSED_CAPABILITY_QUESTION.fullmatch(query)
    general = _GENERAL_CAPABILITY_QUESTION.fullmatch(query)
    if (
        command not in {"/capabilities", "/capabilities all"}
        and not focused
        and not general
    ):
        return None
    include_unavailable = command.endswith(" all") or bool(focused or general)
    report = discover_capabilities(include_unavailable=include_unavailable)
    if focused:
        return _focused_capability_answer(focused.group("subject"), report)
    if general:
        return _natural_capability_summary(report)
    items = report["capabilities"]
    if not items:
        return "No healthy optional capabilities are currently available."
    grouped: dict[str, list[str]] = {}
    for item in items:
        marker = "" if item["available"] else " (unavailable)"
        grouped.setdefault(item["category"], []).append(item["display_name"] + marker)
    lines = [
        f"Curie capability contract {report['contract_version']} ({report['status']})."
    ]
    lines.extend(
        f"{category.title()}: {', '.join(names)}"
        for category, names in sorted(grouped.items())
    )
    return "\n".join(lines)
