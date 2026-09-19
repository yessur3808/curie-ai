"""Intent, approval, and audit adapter for Curie's registered tools."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from types import SimpleNamespace
from typing import Any, Mapping, Optional

from agent.intent_router import ToolRequest, classify_request, resolve_request

__all__ = ["classify_request", "execute_request", "handle_action_request"]

_SECRET_RECEIPT_VALUE = re.compile(
    r"(?i)(bearer\s+)\S+|((?:token|password|secret|api[_-]?key)\s*[:=]\s*)\S+"
)


def _safe_receipt(
    text: str,
    data: Mapping[str, Any],
    source: str | None,
    verification_status: str,
) -> dict[str, Any]:
    from services.audit import redact

    safe_text = _SECRET_RECEIPT_VALUE.sub(
        lambda match: (match.group(1) or match.group(2) or "") + "[REDACTED]",
        str(text),
    )[:4000]
    return {
        "text": safe_text,
        "data": redact(dict(data)),
        "source": str(source or "")[:120],
        "verification_status": verification_status,
    }


def _owner_scope_hash(internal_id: str) -> str:
    return hashlib.sha256(str(internal_id).encode()).hexdigest()[:16]


def _parameter_hash(action: str, params: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"action": action, "params": dict(params)},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _parameter_binding_hash(params: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(params),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _valid_execution_authorization(
    authorization: Mapping[str, Any],
    *,
    internal_id: str,
    connector: str,
    params: Mapping[str, Any],
    idempotency_key: str = "",
) -> bool:
    """Validate every supplied binding before approval or execution."""
    if str(authorization.get("owner_scope_hash") or "") != _owner_scope_hash(
        internal_id
    ):
        return False
    if str(authorization.get("connector") or "") != connector:
        return False
    if str(authorization.get("parameter_hash") or "") != _parameter_binding_hash(
        params
    ):
        return False
    bound_key = str(authorization.get("idempotency_key") or "")
    if bound_key != str(idempotency_key or ""):
        return False
    expires_at = str(authorization.get("expires_at") or "")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        if expiry <= datetime.now(timezone.utc):
            return False
    if idempotency_key:
        plan_hash = str(authorization.get("plan_hash") or "")
        step_id = str(authorization.get("step_id") or "")
        if not plan_hash or not step_id or not expires_at:
            return False
        from agent.kernel.execution import stable_idempotency_key

        expected_key = stable_idempotency_key(
            owner_scope_hash=_owner_scope_hash(internal_id),
            plan_hash=plan_hash,
            step_id=step_id,
            target=(
                params.get("target")
                or params.get("targets")
                or params.get("recipient")
                or params.get("participant_id")
                or ""
            ),
            desired_state=(
                params.get("state") or params.get("text") or params.get("request") or ""
            ),
        )
        if expected_key != idempotency_key:
            return False
    return True


def _approval_binding_hash(
    *,
    internal_id: str,
    connector: str,
    action: str,
    params: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> str:
    payload = {
        "owner_scope_hash": _owner_scope_hash(internal_id),
        "connector": connector,
        "action": action,
        "parameter_hash": _parameter_hash(action, params),
        "plan_hash": str(authorization.get("plan_hash") or "standalone"),
        "step_id": str(authorization.get("step_id") or action),
        "expires_at": str(authorization.get("expires_at") or ""),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _valid_approval_binding(
    pending: Mapping[str, Any],
    *,
    internal_id: str,
    connector: str,
    expected_hash: str,
) -> bool:
    authorization = dict(pending.get("authorization") or {})
    if authorization.get("owner_scope_hash") not in {
        None,
        "",
        _owner_scope_hash(internal_id),
    }:
        return False
    if str(authorization.get("connector") or connector) != connector:
        return False
    if authorization.get("parameter_hash") and authorization[
        "parameter_hash"
    ] != _parameter_binding_hash(dict(pending.get("params") or {})):
        return False
    actual = _approval_binding_hash(
        internal_id=internal_id,
        connector=connector,
        action=str(pending.get("action") or ""),
        params=dict(pending.get("params") or {}),
        authorization=authorization,
    )
    return bool(expected_hash) and actual == expected_hash


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
        binding_hash = str((pending or {}).pop("approval_binding_hash", ""))
        connector = str(profile.get("_connector", "unknown"))
        if not pending or not _valid_approval_binding(
            pending,
            internal_id=str(internal_id),
            connector=connector,
            expected_hash=binding_hash,
        ):
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
        connector = str(profile.get("_connector", "unknown"))
        authorization = dict(req.authorization or {})
        authorization.setdefault("owner_scope_hash", _owner_scope_hash(internal_id))
        authorization.setdefault("connector", connector)
        authorization.setdefault(
            "parameter_hash",
            _parameter_binding_hash(req.params),
        )
        if req.idempotency_key:
            authorization.setdefault("idempotency_key", req.idempotency_key)
        if not _valid_execution_authorization(
            authorization,
            internal_id=str(internal_id),
            connector=connector,
            params=req.params,
            idempotency_key=req.idempotency_key,
        ):
            _audit(
                internal_id,
                req.action,
                "failed",
                connector=connector,
                policy_decision="plan_binding_denied",
                security_category="authorization_failure",
            )
            _capture(
                _outcome,
                status="failed",
                capability=req.action,
                verification_status="not_run",
                error_type="invalid_plan_binding",
            )
            return (
                "I couldn't authorize that action because its plan binding no "
                "longer matches. Nothing was sent."
            )
        pending_action = {
            "action": req.action,
            "params": req.params,
            "needs_approval": False,
            "explanation": req.explanation,
            "confidence": req.confidence,
            "source": req.source,
            "classifier_trace": req.classifier_trace,
            "authorization": authorization,
            "idempotency_key": req.idempotency_key,
        }
        pending_action["approval_binding_hash"] = _approval_binding_hash(
            internal_id=str(internal_id),
            connector=connector,
            action=req.action,
            params=req.params,
            authorization=authorization,
        )
        token = _repositories().approvals.create(
            internal_id,
            pending_action,
            max(1, int(os.getenv("CURIE_APPROVAL_TTL_MINUTES", "30"))),
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
                risk="read_only",
                audit_redactions=frozenset(),
                idempotency=SimpleNamespace(mode="read_only", safe_retry=True),
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
        if req.authorization and not _valid_execution_authorization(
            req.authorization,
            internal_id=str(internal_id),
            connector=str(profile.get("_connector", "unknown")),
            params=req.params,
            idempotency_key=req.idempotency_key,
        ):
            raise PermissionError("The execution plan binding is invalid or expired")
        mutation_key = ""
        mutation_request_hash = ""
        if definition.risk == "mutating" and req.idempotency_key:
            mutation_key = req.idempotency_key
            mutation_request_hash = _parameter_hash(req.action, req.params)
            existing = _repositories().mutations.reserve(
                str(internal_id),
                mutation_key,
                req.action,
                mutation_request_hash,
            )
            if existing:
                if (
                    existing.get("capability") != req.action
                    or existing.get("request_hash") != mutation_request_hash
                ):
                    raise PermissionError(
                        "Idempotency key is bound to a different mutation"
                    )
                receipt = existing.get("receipt") or {}
                if existing.get("status") == "started":
                    _capture(
                        _outcome,
                        status="in_progress",
                        capability=req.action,
                        verification_status="not_run",
                        idempotent_replay=True,
                    )
                    return (
                        "That action is already being processed. I won't send a "
                        "duplicate."
                    )
                if existing.get("status") == "completed" and receipt.get("text"):
                    _capture(
                        _outcome,
                        status="completed",
                        capability=req.action,
                        verification_status=str(
                            receipt.get("verification_status") or "unverified"
                        ),
                        data=dict(receipt.get("data") or {}),
                        idempotent_replay=True,
                    )
                    return str(receipt["text"])
                if definition.idempotency.mode not in {
                    "state_reconciled",
                    "provider_key",
                }:
                    _capture(
                        _outcome,
                        status="uncertain",
                        capability=req.action,
                        verification_status="unverified",
                        idempotent_replay=True,
                    )
                    return (
                        "That mutation may already have been submitted. I won't send "
                        "a duplicate until its prior receipt or state can be checked."
                    )
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
        if mutation_key:
            _repositories().mutations.finish(
                str(internal_id),
                mutation_key,
                "completed",
                _safe_receipt(
                    result,
                    tool_result.data,
                    tool_result.source,
                    verification_status,
                ),
            )
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
        mutation_key = str(locals().get("mutation_key") or "")
        definition = locals().get("definition")
        if mutation_key:
            mode = getattr(getattr(definition, "idempotency", None), "mode", "none")
            _repositories().mutations.finish(
                str(internal_id),
                mutation_key,
                (
                    "failed"
                    if mode in {"state_reconciled", "provider_key"}
                    else "uncertain"
                ),
                {"error_type": type(exc).__name__},
            )
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
            retryable=bool(getattr(exc, "retryable", False))
            or isinstance(exc, (TimeoutError, ConnectionError)),
        )

        return user_facing_tool_error(exc, req.action)


async def handle_action_request(
    text: str, internal_id: str, profile: dict
) -> Optional[str]:
    request = await resolve_request(text)
    if request is None:
        return None
    return await execute_request(request, str(internal_id), profile or {})
