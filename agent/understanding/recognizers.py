"""High-precision recognizers that emit evidence, never user-facing prose."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from .taxonomy import INTENT_TAXONOMY, TAXONOMY_VERSION, IntentLeaf


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    start: int
    end: int
    label: str
    value: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Evidence spans require a non-empty source range")

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "label": self.label,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class Recognition:
    intent: IntentLeaf
    confidence: float
    evidence: tuple[EvidenceSpan, ...]
    entities: Mapping[str, Any] = field(default_factory=dict)
    capability_candidates: tuple[str, ...] = ()
    risk: str = "none"
    recognizer: str = "deterministic"
    taxonomy_version: str = TAXONOMY_VERSION

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Recognition confidence must be between zero and one")
        if not self.evidence:
            raise ValueError("Deterministic recognition requires source evidence")

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "evidence": [item.as_dict() for item in self.evidence],
            "entity_keys": sorted(self.entities),
            "capability_candidates": list(self.capability_candidates),
            "risk": self.risk,
            "recognizer": self.recognizer,
            "taxonomy_version": self.taxonomy_version,
        }


_EMERGENCY = re.compile(
    r"^\s*(?:emergency\s+stop|stop\s+everything|abort\s+all(?:\s+tasks)?|kill\s+all\s+tasks)(?:\s+now)?\s*[.!?]*$",
    re.I,
)
_APPROVE = re.compile(r"^\s*/approve\s+action\s+([a-f0-9]{8})\s*$", re.I)
_REJECT = re.compile(r"^\s*/reject\s+action\s+([a-f0-9]{8})\s*$", re.I)
_TASK = re.compile(
    r"^\s*/?task\s+(inspect|status|cancel|pause|resume)\b(?:\s+([a-f0-9]{8,64}))?",
    re.I,
)
_MEMORY = (
    (
        IntentLeaf.MEMORY_REMEMBER,
        re.compile(r"^\s*(?:please\s+)?remember\s+(?:that\s+)?(.+)", re.I),
    ),
    (
        IntentLeaf.MEMORY_RECALL,
        re.compile(r"^\s*(?:what|show|tell)\b.{0,40}\bremember\b", re.I),
    ),
    (IntentLeaf.MEMORY_FORGET, re.compile(r"^\s*(?:please\s+)?forget\s+(.+)", re.I)),
    (
        IntentLeaf.MEMORY_EXPORT,
        re.compile(r"^\s*export\b.{0,50}\b(?:memory|memories|remember)\b", re.I),
    ),
    (
        IntentLeaf.MEMORY_CORRECT,
        re.compile(
            r"^\s*(?:that(?:'s| is)\s+wrong|correction[:,]?|there\s+is\s+no\s+device\s+(?:called|named)\s+)(.+)",
            re.I,
        ),
    ),
)
_DEVICE_MUTATION = re.compile(
    r"\b(?:(turn|switch|power)\s+(?:(on|off)\s+)?(.+?)(?:\s+(on|off))?|"
    r"(kill)\s+(?:all\s+|the\s+)?(.+?))\s*[.!?]*$",
    re.I,
)
_DEVICE_READ = re.compile(
    r"\b(?:home\s+(?:status|summary)|(?:is|are|check|show|get|list)\b.{0,80}"
    r"\b(?:lights?|lamps?|devices?|plugs?|switches?)\b.{0,30}"
    r"\b(?:on|off|online|offline|status|running)\b)",
    re.I,
)
_CALCULATION = re.compile(
    r"^\s*(?:what(?:'s|\s+is)|calculate|compute)?\s*"
    r"(?:\(?\s*[-+]?\d[\d.,]*\s*[-+*/%^]\s*[-+]?\d[\d.,]*|"
    r"\d+(?:\.\d+)?\s+percent\s+of\s+\d+(?:\.\d+)?)",
    re.I,
)
_CONVERSION = re.compile(
    r"\b(?:convert\s+)?[-+]?\d[\d.,]*\s*[a-z°$€£]+\s+(?:to|into|in)\s+[a-z°$€£]+\b",
    re.I,
)
_HEALTH = re.compile(r"\b(?:health|healthy|system\s+status|are\s+you\s+okay)\b", re.I)
_CAPABILITIES = re.compile(
    r"\b(?:what\s+can\s+you\s+do|capabilit(?:y|ies)|available\s+commands)\b", re.I
)
_SYSTEM_INSPECT = re.compile(
    r"\b(?:check|show|inspect)\b.{0,40}\b(?:ram|memory\s+usage|hardware|network\s+speed|latency)\b",
    re.I,
)
_NON_COMMAND_CONTEXT = re.compile(
    r"\b(?:the\s+phrase|quoted?|example|story|novel|metaphor|"
    r"discuss(?:ed|ing)?|talk(?:ed|ing)?\s+about|hypothetical)\b",
    re.I,
)


def _span(match: re.Match[str], label: str, value: str | None = None) -> EvidenceSpan:
    return EvidenceSpan(match.start(), match.end(), label, value or match.group(0))


def _recognition(
    leaf: IntentLeaf,
    match: re.Match[str],
    *,
    confidence: float = 1.0,
    entities: Mapping[str, Any] | None = None,
    label: str = "intent_trigger",
) -> Recognition:
    definition = INTENT_TAXONOMY[leaf]
    return Recognition(
        leaf,
        confidence,
        (_span(match, label),),
        dict(entities or {}),
        definition.candidate_capabilities,
        definition.risk,
    )


def recognize_deterministic(
    text: str,
    *,
    pending_approval: bool = False,
    active_task: bool = False,
) -> Recognition | None:
    """Return a high-precision structured recognition or abstain."""
    source = str(text or "")
    if not source.strip():
        return None
    if match := _EMERGENCY.fullmatch(source):
        return _recognition(IntentLeaf.EMERGENCY_STOP, match)
    if match := _APPROVE.fullmatch(source):
        return _recognition(
            IntentLeaf.APPROVAL,
            match,
            entities={"approval_token": match.group(1)},
        )
    if match := _REJECT.fullmatch(source):
        return _recognition(
            IntentLeaf.REJECTION,
            match,
            entities={"approval_token": match.group(1)},
        )
    normalized = source.strip().casefold().rstrip(".!?")
    if pending_approval and normalized in {"yes", "approve", "go ahead", "do it"}:
        match = re.search(r"\S[\s\S]*", source)
        return _recognition(IntentLeaf.APPROVAL, match) if match else None
    if pending_approval and normalized in {"no", "deny", "reject", "don't", "do not"}:
        match = re.search(r"\S[\s\S]*", source)
        return _recognition(IntentLeaf.REJECTION, match) if match else None
    if match := _TASK.match(source):
        action, task_id = match.group(1).casefold(), match.group(2)
        leaf = (
            IntentLeaf.CANCELLATION
            if action in {"cancel", "pause"}
            else IntentLeaf.TASK_STATUS
        )
        return _recognition(
            leaf, match, entities={"task_action": action, "task_id": task_id}
        )
    if active_task and normalized in {
        "cancel",
        "cancel it",
        "pause",
        "resume",
        "status",
    }:
        match = re.search(r"\S[\s\S]*", source)
        leaf = (
            IntentLeaf.CANCELLATION
            if normalized.startswith(("cancel", "pause"))
            else IntentLeaf.TASK_STATUS
        )
        return _recognition(leaf, match) if match else None
    for leaf, pattern in _MEMORY:
        if match := pattern.search(source):
            return _recognition(
                leaf,
                match,
                entities={"content": match.group(1) if match.lastindex else ""},
            )
    if _NON_COMMAND_CONTEXT.search(source):
        return None
    if match := _DEVICE_MUTATION.search(source):
        state = (
            match.group(2) or match.group(4) or ("off" if match.group(5) else "")
        ).casefold()
        target = (match.group(3) or match.group(6) or "").strip(" \t.,!?")
        if state and target:
            return _recognition(
                IntentLeaf.DEVICE_STATE_MUTATION,
                match,
                entities={"device_target": target, "desired_state": state},
            )
    if match := _DEVICE_READ.search(source):
        return _recognition(IntentLeaf.DEVICE_STATE_READ, match, confidence=0.99)
    if match := _CALCULATION.search(source):
        return _recognition(
            IntentLeaf.CALCULATION, match, entities={"expression": match.group(0)}
        )
    if match := _CONVERSION.search(source):
        return _recognition(
            IntentLeaf.CONVERSION, match, entities={"expression": match.group(0)}
        )
    if match := _HEALTH.search(source):
        return _recognition(IntentLeaf.HEALTH, match, confidence=0.99)
    if match := _CAPABILITIES.search(source):
        return _recognition(IntentLeaf.CAPABILITY_HELP, match, confidence=0.99)
    if match := _SYSTEM_INSPECT.search(source):
        return _recognition(IntentLeaf.LOCAL_SYSTEM_INSPECTION, match, confidence=0.99)
    if source.lstrip().startswith("/"):
        match = re.search(r"/\S+", source)
        if match:
            return _recognition(
                IntentLeaf.UNSUPPORTED, match, confidence=0.98, label="slash_command"
            )
    return None
