"""Typed response planning and rendering with a semantic fact lock.

The response planner owns *what* Curie must communicate.  The renderer owns
only wording, layout, and connector-specific message boundaries.  Keeping the
two contracts separate prevents a style pass from changing device states,
counts, identifiers, limitations, or failures after execution has finished.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


class DetailLevel(str, Enum):
    TERSE = "terse"
    SHORT = "short"
    STANDARD = "standard"
    DETAILED = "detailed"


class OutcomeType(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    ALREADY_SATISFIED = "already_satisfied"
    MIXED = "mixed"
    PARTIAL_FAILURE = "partial_failure"
    FAILURE = "failure"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    INFORMATIONAL = "informational"


_DETAILED = re.compile(
    r"\b(?:detailed|thorough(?:ly)?|comprehensive|deep dive|step[- ]by[- ]step|"
    r"all (?:the )?details|explain fully)\b",
    re.I,
)
_BRIEF = re.compile(
    r"\b(?:brief|briefly|short answer|quick answer|just tell me|tl;?dr|concise)\b",
    re.I,
)
_UNSUPPORTED = re.compile(
    r"\b(?:unsupported|not supported|cannot do that|can't do that|"
    r"do not have an executor|no executor|not available)\b",
    re.I,
)
_SEMANTIC_KEY = re.compile(
    r"(?:^|_)(?:id|ids|name|names|count|state|status|verification|verified|"
    r"failure|failed|error|limit|limitation|capability|supported|unsupported|"
    r"retryable|target|targets|device|devices|result|outcome)(?:$|_)",
    re.I,
)
_NON_SEMANTIC_ROOTS = frozenset(
    {
        "text",
        "message_parts",
        "timestamp",
        "processing_time_ms",
        "timings_ms",
        "model_used",
        "provenance",
        "pipeline",
        "response_plan",
        "_response_plan",
        "response_origin",
    }
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def semantic_facts(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract immutable execution facts without treating prose as evidence."""

    def visit(value: Any, path: tuple[str, ...]) -> Any:
        if isinstance(value, Mapping):
            selected: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                if not path and key in _NON_SEMANTIC_ROOTS:
                    continue
                child = visit(item, (*path, key))
                if child is not None:
                    selected[key] = child
            return selected or None
        if isinstance(value, (list, tuple)):
            children = [
                visit(item, (*path, str(index))) for index, item in enumerate(value)
            ]
            kept = [item for item in children if item is not None]
            return kept or None
        if path and any(_SEMANTIC_KEY.search(part) for part in path):
            return _json_value(value)
        return None

    return visit(result, ()) or {}


def fact_fingerprint(facts: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _json_value(facts), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _outcome_type(result: Mapping[str, Any]) -> OutcomeType:
    verification = str(result.get("verification_status") or "").casefold()
    execution_status = str(result.get("execution_status") or "").casefold()
    text = str(result.get("text") or "")
    execution = result.get("execution")
    outcomes = execution.get("outcomes", ()) if isinstance(execution, Mapping) else ()
    statuses = {
        str(item.get("status") or "").casefold()
        for item in outcomes
        if isinstance(item, Mapping)
    }
    failures = statuses & {"failed", "rejected", "skipped_dependency", "cancelled"}
    successes = statuses & {"completed", "verified", "already_satisfied"}
    if failures and successes:
        return OutcomeType.PARTIAL_FAILURE
    if failures or execution_status in {"failed", "partial_failure"}:
        return OutcomeType.FAILURE
    if verification == "already_satisfied":
        return OutcomeType.ALREADY_SATISFIED
    if verification == "verified":
        return OutcomeType.VERIFIED_SUCCESS
    if verification in {"contradicted", "unverified", "completed_unverified"}:
        return OutcomeType.AMBIGUOUS
    if _UNSUPPORTED.search(text):
        return OutcomeType.UNSUPPORTED
    if len(statuses) > 1:
        return OutcomeType.MIXED
    return OutcomeType.INFORMATIONAL


def choose_detail_level(
    user_text: str,
    *,
    response_mode: str = "brief",
    outcome: OutcomeType = OutcomeType.INFORMATIONAL,
    part_count: int = 1,
) -> DetailLevel:
    """Choose detail independently from personality or factual content."""
    if _BRIEF.search(user_text):
        return DetailLevel.TERSE
    if _DETAILED.search(user_text) or response_mode == "deep":
        return DetailLevel.DETAILED
    if outcome in {
        OutcomeType.FAILURE,
        OutcomeType.PARTIAL_FAILURE,
        OutcomeType.AMBIGUOUS,
    }:
        return DetailLevel.STANDARD
    if response_mode in {"command_ack", "social"}:
        return DetailLevel.TERSE
    if response_mode in {"focused", "status"} or part_count > 2:
        return DetailLevel.STANDARD
    return DetailLevel.SHORT


def _message_parts(result: Mapping[str, Any], text: str) -> tuple[str, ...]:
    raw = result.get("message_parts")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        parts = tuple(str(item).strip() for item in raw if str(item).strip())
        if parts:
            return parts
    return (text,) if text else ()


@dataclass(frozen=True, slots=True)
class ResponsePlan:
    outcome: OutcomeType
    detail: DetailLevel
    source_text: str
    message_parts: tuple[str, ...]
    verification_status: str
    needs_question: bool
    connector: str
    response_mode: str
    facts: Mapping[str, Any]
    fact_fingerprint: str
    personality: str = "warm_casual_direct"
    french_usage: str = "light_contextual_only"

    def as_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "outcome": self.outcome.value,
            "detail": self.detail.value,
            "verification_status": self.verification_status,
            "needs_question": self.needs_question,
            "connector": self.connector,
            "response_mode": self.response_mode,
            "fact_fingerprint": self.fact_fingerprint,
            "personality": self.personality,
            "french_usage": self.french_usage,
            "message_part_count": len(self.message_parts),
        }
        if include_content:
            payload.update(
                {
                    "source_text": self.source_text,
                    "message_parts": list(self.message_parts),
                    "facts": _json_value(self.facts),
                }
            )
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResponsePlan":
        return cls(
            outcome=OutcomeType(str(payload["outcome"])),
            detail=DetailLevel(str(payload["detail"])),
            source_text=str(payload.get("source_text") or ""),
            message_parts=tuple(str(item) for item in payload.get("message_parts", ())),
            verification_status=str(payload.get("verification_status") or ""),
            needs_question=bool(payload.get("needs_question")),
            connector=str(payload.get("connector") or "unknown"),
            response_mode=str(payload.get("response_mode") or "brief"),
            facts=dict(payload.get("facts") or {}),
            fact_fingerprint=str(payload["fact_fingerprint"]),
            personality=str(payload.get("personality") or "warm_casual_direct"),
            french_usage=str(payload.get("french_usage") or "light_contextual_only"),
        )


def build_response_plan(
    result: Mapping[str, Any],
    *,
    user_text: str,
    response_mode: str,
    connector: str,
) -> ResponsePlan:
    text = str(result.get("text") or "").strip()
    parts = _message_parts(result, text)
    outcome = _outcome_type(result)
    facts = semantic_facts(result)
    return ResponsePlan(
        outcome=outcome,
        detail=choose_detail_level(
            user_text,
            response_mode=response_mode,
            outcome=outcome,
            part_count=len(parts),
        ),
        source_text=text,
        message_parts=parts,
        verification_status=str(result.get("verification_status") or ""),
        needs_question=response_mode == "clarification",
        connector=str(connector or "unknown"),
        response_mode=str(response_mode or "brief"),
        facts=facts,
        fact_fingerprint=fact_fingerprint(facts),
        french_usage=(
            "none"
            if response_mode in {"command_ack", "status"}
            else "light_contextual_only"
        ),
    )


def render_outcome_template(plan: ResponsePlan) -> str:
    """Render a conservative fallback without inventing targets or states."""
    return {
        OutcomeType.VERIFIED_SUCCESS: "Done. The result is verified.",
        OutcomeType.ALREADY_SATISFIED: "It’s already set that way.",
        OutcomeType.MIXED: "Some parts completed, and the details are below.",
        OutcomeType.PARTIAL_FAILURE: "Part of that worked, but one or more steps failed.",
        OutcomeType.FAILURE: "I couldn’t complete that.",
        OutcomeType.AMBIGUOUS: "The action may have run, but I couldn’t verify the final state.",
        OutcomeType.UNSUPPORTED: "I can’t do that with the capabilities currently available.",
        OutcomeType.INFORMATIONAL: "I have the result, but couldn’t format its details.",
    }[plan.outcome]


def render_response(plan: ResponsePlan, result: Mapping[str, Any]) -> dict[str, Any]:
    """Render the plan and prove that structured execution facts are unchanged."""
    before = fact_fingerprint(semantic_facts(result))
    if before != plan.fact_fingerprint:
        raise ValueError("response facts changed between planning and rendering")

    rendered = dict(result)
    text = plan.source_text or render_outcome_template(plan)
    parts = tuple(item for item in plan.message_parts if item.strip()) or (text,)
    rendered["text"] = text
    rendered["message_parts"] = list(parts)
    rendered["response_plan"] = plan.as_dict()

    after = fact_fingerprint(semantic_facts(rendered))
    if after != plan.fact_fingerprint:
        raise ValueError("response renderer modified an immutable execution fact")
    return rendered


__all__ = [
    "DetailLevel",
    "OutcomeType",
    "ResponsePlan",
    "build_response_plan",
    "choose_detail_level",
    "fact_fingerprint",
    "render_outcome_template",
    "render_response",
    "semantic_facts",
]
