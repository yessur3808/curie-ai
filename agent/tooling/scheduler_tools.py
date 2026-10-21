"""Approval-bound reminder tools backed by Curie's durable task repository."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult


class ReminderListTool:
    name, read_only = "reminder_list", True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from agent.skills.scheduler import list_reminders

        return ToolResult(list_reminders(context.internal_id), {}, "Curie task engine")


class ReminderCreateTool:
    name, read_only = "reminder_create", False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        if not context.approved:
            raise PermissionError("A fresh approval is required to schedule a reminder")
        from agent.skills.scheduler import add_reminder

        due_at = datetime.fromisoformat(str(params["due_at"]).replace("Z", "+00:00"))
        text = add_reminder(
            context.internal_id,
            str(params.get("platform") or context.platform),
            str(params["message"]),
            due_at,
        )
        return ToolResult(text, {"due_at": due_at.isoformat()}, "Curie task engine")


class ReminderDeleteTool:
    name, read_only = "reminder_delete", False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        if not context.approved:
            raise PermissionError("A fresh approval is required to delete reminders")
        from agent.skills.scheduler import delete_reminder

        index = params.get("index")
        return ToolResult(
            delete_reminder(context.internal_id, int(index) if index else None),
            {"index": index},
            "Curie task engine",
        )


__all__ = ["ReminderCreateTool", "ReminderDeleteTool", "ReminderListTool"]
