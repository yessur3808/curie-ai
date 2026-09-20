"""Fail-closed policy for optional credentialed canary evaluations."""

from __future__ import annotations

from typing import Any, Mapping


_FORBIDDEN_DEFAULT = {
    "financial_transaction",
    "crypto_trade",
    "polymarket_order",
    "public_post",
    "send_email",
}


class CanaryPolicyError(PermissionError):
    """Raised when a canary would exceed its explicit harmless scope."""


def validate_canary_plan(
    plan: Mapping[str, Any], *, explicitly_enabled: bool = False
) -> dict[str, Any]:
    """Allow only named sandbox resources after an explicit test opt-in."""
    if not explicitly_enabled:
        raise CanaryPolicyError("credentialed canaries are disabled by default")
    capabilities = {str(item) for item in plan.get("capabilities", ())}
    forbidden = capabilities & _FORBIDDEN_DEFAULT
    if forbidden:
        raise CanaryPolicyError(
            "credentialed canaries cannot use: " + ", ".join(sorted(forbidden))
        )
    resources = [str(item).strip() for item in plan.get("sandbox_resources", ())]
    if not resources or any(not item for item in resources):
        raise CanaryPolicyError("canaries require explicit harmless sandbox resources")
    if not bool(plan.get("cleanup_required", False)):
        raise CanaryPolicyError("canaries require a cleanup contract")
    return {
        "enabled": True,
        "capabilities": sorted(capabilities),
        "sandbox_resources": resources,
        "cleanup_required": True,
    }


__all__ = ["CanaryPolicyError", "validate_canary_plan"]
