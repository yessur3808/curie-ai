"""Canonical machine-readable contracts and truthful capability discovery."""

from __future__ import annotations

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


def handle_capabilities_command(text: str) -> str | None:
    command = text.strip().casefold()
    if command not in {"/capabilities", "/capabilities all"}:
        return None
    report = discover_capabilities(include_unavailable=command.endswith(" all"))
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
