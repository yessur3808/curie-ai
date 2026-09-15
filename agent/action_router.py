"""Intent, approval, and audit adapter for Curie's registered tools."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from types import SimpleNamespace
from typing import Any, Mapping, Optional

from agent.intent_router import ToolRequest, classify_request, resolve_request


def _repositories():
    # Resolve lazily so test isolation and runtime backend resets cannot leave
    # this long-lived router holding a stale repository module or singleton.
    from memory.repositories import get_repositories

    return get_repositories()


def _audit(user: str, action: str, status: str, **details: Any) -> None:
    _repositories().audits.append(user, action, status, details)


def _capture(outcome: dict | None, **values: Any) -> None:
    if outcome is not None:
        outcome.update(values)


def _verification_status(action: str, data: Mapping[str, Any]) -> str:
    """Classify destination evidence without parsing user-facing prose."""
    explicit = str(data.get("verification_status") or "").strip().casefold()
    if explicit in {
        "verified",
        "already_satisfied",
        "unverified",
        "contradicted",
        "failed",
    }:
        return explicit
    if data.get("already_in_state") is True:
        return "already_satisfied"
    receipts = list(data.get("receipts") or ())
    receipt = data.get("receipt")
    if isinstance(receipt, Mapping):
        receipts.append(receipt)
    states = [
        (
            str(item.get("requested_state") or ""),
            str(item.get("verified_state") or "unknown"),
        )
        for item in receipts
        if isinstance(item, Mapping)
    ]
    if states and all(requested == verified for requested, verified in states):
        return "verified"
    if any(verified in {"on", "off"} for _, verified in states):
        return "contradicted"
    if states:
        return "unverified"
    try:
        from agent.tooling import get_runtime_registry

        return (
            "completed_unverified"
            if get_runtime_registry().get(action).risk == "mutating"
            else "not_required"
        )
    except Exception:
        return "unknown"


async def execute_request(
    req: ToolRequest,
    internal_id: str,
    profile: dict,
    *,
    approved: bool = False,
    _outcome: dict | None = None,
) -> str:
    if req.action == "clarify":
        _capture(
            _outcome,
            status="clarification",
            capability=req.action,
            verification_status="not_run",
        )
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
                approval={
                    "required": True,
                    "granted": False,
                    "token": req.params["token"],
                },
                outcome={"status": "invalid_or_expired"},
                security_category="approval_failure",
            )
            _capture(
                _outcome,
                status="failed",
                capability="approval",
                verification_status="failed",
                error_type="invalid_approval",
            )
            return "That approval token is invalid, expired, already used, or belongs to another user."
        if not approved:
            _audit(
                internal_id,
                pending["action"],
                "rejected",
                approval={
                    "required": True,
                    "granted": False,
                    "token": req.params["token"],
                },
                security_category="policy_denial",
            )
            _capture(
                _outcome,
                status="rejected",
                capability=str(pending["action"]),
                verification_status="not_run",
            )
            return "Action rejected. No changes were made."
        restored = ToolRequest(**pending)
        restored.needs_approval = False
        return await execute_request(
            restored,
            internal_id,
            profile,
            approved=True,
            _outcome=_outcome,
        )
    if req.needs_approval:
        token = _repositories().approvals.create(
            internal_id,
            {
                "action": req.action,
                "params": req.params,
                "needs_approval": False,
                "explanation": req.explanation,
            },
        )
        from agent.tooling import get_runtime_registry

        definition = get_runtime_registry().get(req.action)
        safe_params = {
            key: "[REDACTED]" if key in definition.audit_redactions else value
            for key, value in req.params.items()
        }
        _audit(
            internal_id,
            req.action,
            "pending",
            parameters=safe_params,
            policy_decision="approval_required",
            approval={"required": True, "granted": False, "token": token},
            connector=profile.get("_connector", "unknown"),
        )
        _capture(
            _outcome,
            status="waiting_approval",
            capability=req.action,
            verification_status="not_run",
            data={"approval_token": token},
        )
        return (
            f"I can {req.explanation}. This is an external action. "
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
        safe_params = {
            key: (
                "[REDACTED]"
                if key in getattr(definition, "audit_redactions", ())
                else value
            )
            for key, value in req.params.items()
        }
        tool_result = await registry.execute(
            req.action,
            req.params,
            ToolContext(
                internal_id=str(internal_id),
                profile=profile,
                platform=str(profile.get("_connector", "unknown")),
                approved=approved,
            ),
        )
        result = tool_result.text
        verification_status = _verification_status(req.action, tool_result.data)
        citations = re.findall(r"https?://[^\s)]+", result)[:20]
        _audit(
            internal_id,
            req.action,
            "completed",
            connector=profile.get("_connector", "unknown"),
            validated_action=req.action,
            parameters=safe_params,
            policy_decision="allowed",
            approval={
                "required": definition.approval_policy != "never",
                "granted": approved,
            },
            tool_version=definition.version,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            changed_files=list(tool_result.data.get("changed_files", [])),
            command_exit_status=(
                tool_result.data.get("exit_status")
                if "command" in tool_result.data
                else None
            ),
            citations=citations,
            outcome={"status": "completed", "summary": result[:1000]},
            verification_status=verification_status,
        )
        _capture(
            _outcome,
            status=(
                "already_satisfied"
                if verification_status == "already_satisfied"
                else ("verified" if verification_status == "verified" else "completed")
            ),
            capability=req.action,
            verification_status=verification_status,
            data=dict(tool_result.data),
            source=tool_result.source,
            retryable=False,
        )
        return result
    except KeyError:
        _capture(
            _outcome,
            status="failed",
            capability=req.action,
            verification_status="failed",
            error_type="missing_executor",
        )
        return "I do not have an executor for that action."
    except Exception as exc:
        redactions = getattr(locals().get("definition"), "audit_redactions", ())
        safe_params = {
            key: "[REDACTED]" if key in redactions else value
            for key, value in req.params.items()
        }
        _audit(
            internal_id,
            req.action,
            "failed",
            connector=profile.get("_connector", "unknown"),
            validated_action=req.action,
            parameters=safe_params,
            policy_decision="execution_failed",
            approval={"required": req.needs_approval, "granted": approved},
            finished_at=datetime.now(timezone.utc),
            outcome={"status": "failed", "error": str(exc)},
            security_category="tool_failure",
        )
        from agent.tooling.errors import user_facing_tool_error

        _capture(
            _outcome,
            status="failed",
            capability=req.action,
            verification_status="failed",
            error_type=type(exc).__name__,
            retryable=bool(getattr(exc, "retryable", False)),
        )

        return user_facing_tool_error(exc, req.action)


async def handle_action_request(
    text: str, internal_id: str, profile: dict
) -> Optional[str]:
    request = await resolve_request(text)
    if request is None:
        return None
    return await execute_request(request, str(internal_id), profile or {})
