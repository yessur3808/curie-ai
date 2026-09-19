"""Local control capabilities that operate on Curie's own task runtime."""

from __future__ import annotations

from agent.kernel.cancellation import get_cancellation_registry
from agent.tooling.contracts import ToolContext, ToolResult


class EmergencyStopTool:
    name = "emergency_stop"
    read_only = False

    async def execute(self, params, context: ToolContext) -> ToolResult:
        owner_id = str(context.internal_id)
        active_plans = get_cancellation_registry().cancel(owner_id)

        from agent.task_runtime import get_task_runtime
        from memory.local_store import list_durable_tasks

        cancelled_tasks: list[str] = []
        for task in list_durable_tasks(owner_id):
            if task.get("status") in {"completed", "failed", "cancelled", "expired"}:
                continue
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            get_task_runtime().cancel(owner_id, task_id)
            cancelled_tasks.append(task_id)

        total = active_plans + len(cancelled_tasks)
        if total:
            text = (
                f"Stopped {total} active task{'s' if total != 1 else ''}. "
                "Anything that had already finished remains recorded."
            )
            verification_status = "verified"
        else:
            text = "Nothing active to stop."
            verification_status = "already_satisfied"
        return ToolResult(
            text,
            {
                "active_plans_signalled": active_plans,
                "durable_tasks_cancelled": cancelled_tasks,
                "verification_status": verification_status,
            },
            "local_task_runtime",
        )
