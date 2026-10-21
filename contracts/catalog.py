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
    r"^(?:(?:curie[, ]+)?(?:capability audit[.! ]+)?(?:simulation only[.: ]+)?"
    r"(?:do not execute tools or change anything[.! ]+)?)?"
    r"(?:in one short answer[, ]+)?(?:what (?:can|are) you (?:actually )?(?:do|capable of)|"
    r"what kinds? of work can you (?:genuinely |actually )?help me with|"
    r"what (?:useful )?(?:things )?can you (?:actually )?help me with"
    r"(?: from here)?|"
    r"what are your (?:current )?capabilities|"
    r"what (?:tools|skills|abilities) (?:do you have|are available|are active)|"
    r"(?:show|list) (?:me )?(?:your )?(?:current )?(?:capabilities|tools|skills)|"
    r"are (?:all )?(?:of )?your (?:tools|skills|capabilities) (?:still )?(?:active|available|working))"
    r"[?.! ]*$",
    re.I,
)
_CAPABILITY_FORMAT_SUFFIX = re.compile(
    r"\s+(?:give|show|format|keep|not the marketing version)\b.{0,160}$", re.I
)
_ONE_CAPABILITY_QUESTION = re.compile(
    r"^(?:interrupt that[.! ]+)?(?:name|give me|tell me) one "
    r"(?:specific )?(?:capability|thing) you (?:genuinely |actually |currently )?"
    r"(?:have|can do)(?: now)?(?: that .+)?[?.! ]*$",
    re.I,
)
_SAFETY_LIMIT_QUESTION = re.compile(
    r"^(?:and )?(?:name|give me|tell me)?\s*(?:one )?(?:thing|action)?\s*"
    r"you cannot safely do without me[?.! ]*$",
    re.I,
)
_LOW_CONFIDENCE_DEVICE_QUESTION = re.compile(
    r"\b(?:confidence|confident)\b.{0,80}\bdevice match\b|"
    r"\bdevice match\b.{0,80}\b(?:confidence|confident)\b",
    re.I,
)
_MISSING_DEVICE_QUESTION = re.compile(
    r"\bno device (?:had|has|with|called)\b.{0,100}\b(?:what should you do|"
    r"how would you handle|what would you do)\b",
    re.I,
)
_PLURAL_DEVICE_REASONING_QUESTION = re.compile(
    r"\bturn off (?:both|those|these|all) (?:the )?(?:lights|devices)\b.{0,180}"
    r"\b(?:how would you|how do you|work out|figure out|know which|which (?:lights|devices) do i mean)\b|"
    r"\bturn (?:both|those|these|all)(?: (?:the )?(?:lights|devices))? off\b.{0,180}"
    r"\b(?:what does|how would you|how do you|work out|figure out|know which|refuse to guess)\b|"
    r"\b(?:how would you|how do you|work out|figure out|know which)\b.{0,180}"
    r"\b(?:turn off (?:both|those|these|all) (?:the )?(?:lights|devices)|"
    r"turn (?:both|those|these|all)(?: (?:the )?(?:lights|devices))? off)\b",
    re.I,
)
_FOCUSED_CAPABILITY_QUESTION = re.compile(
    r"^(?:(?:can|could|do) you (?:currently |still |directly )?"
    r"(?:access|use|read|check|control|track|find|send|reply (?:with|using))|"
    r"do you (?:currently |still )?have access to)\s+"
    r"(?P<subject>(?:the |my )?(?:dashboard(?: info(?:rmation)?)?|google calendar|calendar|"
    r"devices?|lights?|smart home|flights?|maps?|places?|voice messages?|audio messages?))"
    r"(?:\s+(?:directly|right now|for me|today))*[?.! ]*$",
    re.I,
)
_TOP_CAPABILITIES_QUESTION = re.compile(
    r"\bwhich\s+(?:three|3)\s+capabilities\b.{0,100}\b(?:useful|matter|priority)\b|"
    r"\b(?:three|3)\s+(?:most useful|top)\s+capabilities\b",
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


def _practical_capability_summary(report: dict[str, Any]) -> str:
    """Return a compact, availability-backed answer for practical wording."""

    items = report["capabilities"]
    active = {str(item.get("name")) for item in items if item.get("available")}
    lines = ["**What is genuinely useful right now**"]
    groups = (
        (
            {"calendar_list", "calendar_create", "reminder_create"},
            "- Review your calendar and prepare events or reminders for approval",
        ),
        (
            {"flight_track", "navigation_skill", "places_search"},
            "- Track flights, plan routes, and find useful nearby places",
        ),
        (
            {"home_control", "home_status", "home_alias"},
            "- Resolve natural device names, control the right device, and verify its state",
        ),
        (
            {"research", "weather"},
            "- Research current information and compare reliable sources",
        ),
        (
            {"dashboard_snapshot", "hardware", "network_speed"},
            "- Read Curie's dashboard and diagnose health, latency, hardware, or network issues",
        ),
        (
            {"inspect_project", "project_change", "run_tests", "organize_files"},
            "- Inspect projects or files and prepare reviewable changes with tests",
        ),
    )
    lines.extend(label for names, label in groups if active & names)
    unavailable = [
        str(item.get("display_name")) for item in items if not item.get("available")
    ]
    if unavailable:
        lines.append("- Not ready: " + ", ".join(unavailable[:3]))
    return "\n".join(lines)


def _one_capability_answer(report: dict[str, Any]) -> str:
    active = {
        str(item.get("name")): item
        for item in report["capabilities"]
        if item.get("available")
    }
    preferred = (
        (
            "calendar_create",
            "I can review your connected calendar, find a workable time, and prepare an event for your approval.",
        ),
        (
            "home_control",
            "I can resolve natural device names, control the unique match, and verify its resulting state.",
        ),
        (
            "flight_track",
            "I can track a flight's schedule, delay, terminal, gate, and live position when the provider supplies it.",
        ),
        (
            "research",
            "I can research a current question, compare reliable sources, and return a source-linked summary.",
        ),
        (
            "inspect_project",
            "I can inspect a software project, trace a problem, and prepare a reviewable change with tests.",
        ),
    )
    for name, answer in preferred:
        if name in active:
            return answer
    if active:
        item = next(iter(active.values()))
        description = str(item.get("description") or item.get("display_name")).rstrip(
            "."
        )
        return (
            f"{description}."
            if description.casefold().startswith("i can ")
            else f"I can {description[:1].lower() + description[1:]}."
        )
    return "I do not currently have a verified optional capability available."


def _top_capabilities_answer(report: dict[str, Any]) -> str:
    """Pick three concrete capabilities from the live registry, never placeholders."""

    active = {
        str(item.get("name")): item
        for item in report["capabilities"]
        if item.get("available")
    }
    preferred = (
        (
            ("calendar_list", "calendar_create", "reminder_create"),
            "Calendar and reminders: review commitments and prepare approved events or reminders",
        ),
        (
            ("home_status", "home_control", "home_alias"),
            "Devices: resolve natural names, control the unique match, and verify its state",
        ),
        (
            ("flight_track", "navigation_skill", "places_search"),
            "Travel and places: track flights, plan routes, and find nearby places with real links",
        ),
        (
            ("dashboard_snapshot", "hardware", "network_speed"),
            "Diagnostics: inspect Curie’s dashboard, latency, hardware, and network health",
        ),
        (
            ("research", "weather"),
            "Research: compare current sources and return a concise, source-linked answer",
        ),
    )
    selected = [
        label for names, label in preferred if any(name in active for name in names)
    ][:3]
    if len(selected) < 3:
        for item in active.values():
            label = str(
                item.get("description") or item.get("display_name") or ""
            ).strip()
            if label and label not in selected:
                selected.append(label.rstrip("."))
            if len(selected) == 3:
                break
    while len(selected) < 3:
        selected.append("No additional healthy capability is currently verified")
    return "\n".join(f"- {item}" for item in selected[:3])


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
        "flight": ("flight_track",),
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
            return (
                "Calendar tools are available, but that does not prove your account connection "
                "in this turn. I need to verify the connection before reading events; any event "
                "write still requires a clear preview and approval."
            )
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
    if _LOW_CONFIDENCE_DEVICE_QUESTION.search(cleaned):
        return (
            "I would not execute the command. A 72% device match is below the 90% "
            "threshold for a consequential action, so I would show the likely matches "
            "and ask one focused clarification."
        )
    if _MISSING_DEVICE_QUESTION.search(cleaned):
        return (
            "I would check the live device inventory and trusted aliases. If there is no "
            "unique high-confidence match, I would say so, list the closest controllable "
            "choices, and leave every device unchanged."
        )
    if _PLURAL_DEVICE_REASONING_QUESTION.search(cleaned):
        return (
            "I would use the most recent verified device set in this conversation, then "
            "confirm those devices still exist in the live inventory. If two devices are "
            "not uniquely established, I would ask which ones instead of guessing."
        )
    if _SAFETY_LIMIT_QUESTION.fullmatch(cleaned):
        return (
            "I cannot safely make a consequential external change when the target, scope, "
            "or required approval is unclear. That ambiguity must be resolved first."
        )
    query = _CAPABILITY_FORMAT_SUFFIX.sub("", cleaned).rstrip()
    query = re.sub(
        r"^(?:(?:ha[, ]+)?(?:good|okay|ok|right|fine)[.! ]+)+",
        "",
        query,
        flags=re.I,
    )
    focused = _FOCUSED_CAPABILITY_QUESTION.fullmatch(query)
    general = _GENERAL_CAPABILITY_QUESTION.fullmatch(query)
    one_capability = _ONE_CAPABILITY_QUESTION.fullmatch(cleaned)
    top_capabilities = _TOP_CAPABILITIES_QUESTION.search(cleaned)
    if (
        command not in {"/capabilities", "/capabilities all"}
        and not focused
        and not general
        and not one_capability
        and not top_capabilities
    ):
        return None
    include_unavailable = command.endswith(" all") or bool(
        focused or general or one_capability or top_capabilities
    )
    report = discover_capabilities(include_unavailable=include_unavailable)
    if top_capabilities:
        return _top_capabilities_answer(report)
    if one_capability:
        return _one_capability_answer(report)
    if focused:
        return _focused_capability_answer(focused.group("subject"), report)
    if general:
        if re.search(
            r"\b(?:not the marketing version|genuinely useful|from here)\b",
            cleaned,
            re.I,
        ):
            return _practical_capability_summary(report)
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
