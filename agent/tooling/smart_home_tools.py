"""Chat-facing smart-home status and device control capabilities."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult
from agent.tooling.errors import ToolExecutionError


def _require_home_owner(context: ToolContext) -> None:
    configured = os.getenv("MASTER_USER_ID", "").strip()
    if configured and configured != str(context.internal_id):
        raise PermissionError(
            "Smart-home data and controls are available only to Curie's configured owner"
        )


class HomeStatusTool:
    name, read_only = "home_status", True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        _require_home_owner(context)
        from services.smart_home import get_smart_home_hub

        try:
            text, data = await get_smart_home_hub().status(
                context.internal_id,
                str(params.get("target") or "") or None,
                str(params.get("provider") or "") or None,
            )
        except (ValueError, LookupError) as exc:
            raise ToolExecutionError(
                str(exc), user_message=str(exc), retryable=False
            ) from exc
        return ToolResult(text, data, "Smart-home providers")


class HomeControlTool:
    name, read_only = "home_control", False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        _require_home_owner(context)
        from services.smart_home import get_smart_home_hub

        try:
            targets = [str(item) for item in params.get("targets") or ()]
            if targets:
                text, data = await get_smart_home_hub().control_many(
                    context.internal_id,
                    targets,
                    str(params["state"]),
                    str(params.get("provider") or "") or None,
                )
            else:
                text, data = await get_smart_home_hub().control(
                    context.internal_id,
                    str(params.get("target") or ""),
                    str(params["state"]),
                    str(params.get("provider") or "") or None,
                )
        except (ValueError, LookupError, ConnectionError) as exc:
            raise ToolExecutionError(
                str(exc), user_message=str(exc), retryable=False
            ) from exc
        return ToolResult(text, data, "Smart-home providers")


class HomeAliasTool:
    name, read_only = "home_alias", False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        _require_home_owner(context)
        from services.smart_home import get_smart_home_hub

        try:
            text, data = await get_smart_home_hub().learn_alias(
                context.internal_id,
                str(params["device"]),
                str(params["alias"]),
            )
        except (ValueError, LookupError, ConnectionError) as exc:
            raise ToolExecutionError(
                str(exc), user_message=str(exc), retryable=False
            ) from exc
        return ToolResult(text, data, "Curie local memory")


class HomeAliasRejectTool:
    name, read_only = "home_alias_reject", False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        _require_home_owner(context)
        from services.smart_home import get_smart_home_hub

        try:
            text, data = await get_smart_home_hub().reject_alias(
                context.internal_id, str(params["alias"])
            )
        except ValueError as exc:
            raise ToolExecutionError(
                str(exc), user_message=str(exc), retryable=False
            ) from exc
        return ToolResult(text, data, "Curie local memory")
