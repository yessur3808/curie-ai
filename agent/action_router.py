"""Intent, approval, and audit adapter for Curie's registered tools."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from types import SimpleNamespace
from typing import Any, Optional

from agent.intent_router import ToolRequest, classify_request, resolve_request


def _repositories():
    # Resolve lazily so test isolation and runtime backend resets cannot leave
    # this long-lived router holding a stale repository module or singleton.
    from memory.repositories import get_repositories

    return get_repositories()


def _audit(user: str, action: str, status: str, **details: Any) -> None:
    _repositories().audits.append(user, action, status, details)


async def execute_request(req: ToolRequest, internal_id: str, profile: dict, *, approved: bool = False) -> str:
    if req.action == "clarify":
        return str(req.params["message"])
    if req.action in {"approve", "reject"}:
        approved = req.action == "approve"
        pending = _repositories().approvals.consume(
            internal_id, req.params["token"], approved
        )
        if not pending:
            _audit(
                internal_id,
                "approval",
                "failed",
                connector=profile.get("_connector", "unknown"),
                policy_decision="denied",
                approval={"required": True, "granted": False, "token": req.params["token"]},
                outcome={"status": "invalid_or_expired"},
                security_category="approval_failure",
            )
            return "That approval token is invalid, expired, already used, or belongs to another user."
        if not approved:
            _audit(internal_id, pending["action"], "rejected", approval={"required": True, "granted": False, "token": req.params["token"]}, security_category="policy_denial")
            return "Action rejected. No changes were made."
        restored = ToolRequest(**pending)
        restored.needs_approval = False
        return await execute_request(restored, internal_id, profile, approved=True)
    if req.needs_approval:
        token = _repositories().approvals.create(internal_id, {
            "action": req.action, "params": req.params, "needs_approval": False,
            "explanation": req.explanation,
        })
        _audit(internal_id, req.action, "pending", parameters=req.params, policy_decision="approval_required", approval={"required": True, "granted": False, "token": token}, connector=profile.get("_connector", "unknown"))
        return (
            f"I can {req.explanation}. This may modify project files. "
            f"Reply `/approve action {token}` within 30 minutes to proceed, or "
            f"`/reject action {token}` to cancel."
        )
    try:
        from agent.tooling import ToolContext, get_runtime_registry

        started_at = datetime.now(timezone.utc)
        registry = get_runtime_registry()
        definition = (
            registry.get(req.action)
            if hasattr(registry, "get")
            else SimpleNamespace(
                version="unknown",
                approval_policy="per_invocation" if req.needs_approval else "never",
            )
        )
        tool_result = await registry.execute(
            req.action, req.params, ToolContext(internal_id=str(internal_id), profile=profile, approved=approved)
        )
        result = tool_result.text
        citations = re.findall(r"https?://[^\s)]+", result)[:20]
        _audit(
            internal_id, req.action, "completed", connector=profile.get("_connector", "unknown"),
            validated_action=req.action, parameters=req.params, policy_decision="allowed",
            approval={"required": definition.approval_policy != "never", "granted": approved},
            tool_version=definition.version, started_at=started_at, finished_at=datetime.now(timezone.utc),
            changed_files=list(tool_result.data.get("changed_files", [])),
            command_exit_status=tool_result.data.get("exit_status", 0) if "command" in tool_result.data else None, citations=citations,
            outcome={"status": "completed", "summary": result[:1000]},
        )
        return result
    except KeyError:
        return "I do not have an executor for that action."
    except Exception as exc:
        _audit(internal_id, req.action, "failed", connector=profile.get("_connector", "unknown"), validated_action=req.action, parameters=req.params, policy_decision="execution_failed", approval={"required": req.needs_approval, "granted": approved}, finished_at=datetime.now(timezone.utc), outcome={"status": "failed", "error": str(exc)}, security_category="tool_failure")
        return f"I could not complete that action: {exc}"


async def handle_action_request(text: str, internal_id: str, profile: dict) -> Optional[str]:
    request = await resolve_request(text)
    if request is None:
        return None
    return await execute_request(request, str(internal_id), profile or {})
