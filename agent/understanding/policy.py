"""Risk-aware confidence and clarification policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClarificationDecision:
    required: bool
    reason: str
    question_key: str | None = None


def decide_clarification(
    *,
    risk: str,
    plausible_targets: int = 1,
    missing_destination: bool = False,
    missing_risk_parameter: bool = False,
    authority_uncertain: bool = False,
    final_state_unclear: bool = False,
    deterministic_group: bool = False,
    reversible_default: bool = False,
    ordinary_conversation: bool = False,
    capability_unavailable: bool = False,
    useful_read_first: bool = False,
) -> ClarificationDecision:
    if ordinary_conversation:
        return ClarificationDecision(False, "ordinary conversation")
    if capability_unavailable:
        return ClarificationDecision(
            False, "capability health already proves unavailable"
        )
    if deterministic_group and plausible_targets >= 1:
        return ClarificationDecision(False, "deterministic group resolution")
    if useful_read_first and risk in {"none", "read_only"}:
        return ClarificationDecision(False, "safe read can resolve uncertainty")
    if reversible_default and risk != "mutating":
        return ClarificationDecision(False, "safe reversible default")
    if plausible_targets > 1 and risk == "mutating":
        return ClarificationDecision(True, "multiple consequential targets", "target")
    if missing_destination:
        return ClarificationDecision(True, "missing destination", "destination")
    if missing_risk_parameter:
        return ClarificationDecision(
            True, "missing risk-changing parameter", "parameter"
        )
    if authority_uncertain:
        return ClarificationDecision(True, "owner authority is uncertain", "authority")
    if final_state_unclear and risk == "mutating":
        return ClarificationDecision(
            True, "desired final state is unclear", "desired_state"
        )
    return ClarificationDecision(False, "sufficient evidence")
