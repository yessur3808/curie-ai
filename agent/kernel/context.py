"""Explainable context selection with hard per-section token budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import os
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class ContextSection(str, Enum):
    CURRENT_MESSAGE = "current_message"
    UNRESOLVED_GOAL = "unresolved_goal"
    RECENT_VERBATIM = "recent_verbatim"
    ROLLING_SUMMARY = "rolling_summary"
    DURABLE_MEMORY = "durable_memory"
    TOOL_SCHEMA = "tool_schema"
    TOOL_RESULT = "tool_result"
    PERSONA = "persona"


_DEFAULT_SECTION_BUDGETS = {
    ContextSection.CURRENT_MESSAGE: 0,
    ContextSection.UNRESOLVED_GOAL: 384,
    ContextSection.RECENT_VERBATIM: 1600,
    ContextSection.ROLLING_SUMMARY: 600,
    ContextSection.DURABLE_MEMORY: 800,
    ContextSection.TOOL_SCHEMA: 1000,
    ContextSection.TOOL_RESULT: 1200,
    ContextSection.PERSONA: 1800,
}


def estimate_tokens(text: str) -> int:
    """Stable, dependency-free upper approximation for prompt accounting."""

    if not text:
        return 0
    words = len(re.findall(r"\S+", text))
    return max(words, math.ceil(len(text) / 4))


def _truncate_to_tokens(text: str, token_limit: int) -> str:
    if token_limit <= 0:
        return ""
    char_limit = max(1, token_limit * 4)
    if len(text) <= char_limit:
        return text
    marker = " ... [context truncated]"
    body_limit = max(1, char_limit - len(marker))
    body = text[:body_limit].rsplit(" ", 1)[0].rstrip()
    return f"{body}{marker}" if body else marker.strip()


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    id: str
    section: ContextSection
    content: str
    reason: str
    source_turn: str | None = None
    confidence: float = 1.0
    contradicted: bool = False
    rejected: bool = False
    consequential: bool = False
    allow_truncate: bool = True
    payload: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Context candidates require an id")
        if not self.reason.strip():
            raise ValueError("Context candidates require a selection reason")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Context confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class ContextDecision:
    candidate_id: str
    section: ContextSection
    included: bool
    reason: str
    estimated_tokens: int
    truncated: bool = False
    content: str = field(default="", repr=False, compare=False)
    payload: Any = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "section": self.section.value,
            "included": self.included,
            "reason": self.reason,
            "estimated_tokens": self.estimated_tokens,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class ContextEnvelope:
    decisions: tuple[ContextDecision, ...]
    section_budgets: Mapping[ContextSection, int]
    total_budget: int
    selected_tokens: int
    unavoidable_overflow_tokens: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "section_budgets", MappingProxyType(dict(self.section_budgets))
        )

    def included(self, section: ContextSection) -> tuple[ContextDecision, ...]:
        return tuple(
            item for item in self.decisions if item.section is section and item.included
        )

    def text(self, section: ContextSection, separator: str = "\n") -> str:
        return separator.join(item.content for item in self.included(section))

    def as_dict(self) -> dict[str, Any]:
        """Explain selection without exposing prompt or memory contents."""

        return {
            "schema_version": 1,
            "total_budget": self.total_budget,
            "selected_tokens": self.selected_tokens,
            "unavoidable_overflow_tokens": self.unavoidable_overflow_tokens,
            "section_budgets": {
                section.value: value for section, value in self.section_budgets.items()
            },
            "decisions": [item.as_dict() for item in self.decisions],
        }


class ContextBudgeter:
    """Select context deterministically and record why every item was handled."""

    def __init__(
        self,
        *,
        total_budget: int | None = None,
        section_budgets: Mapping[ContextSection, int] | None = None,
    ):
        configured = dict(_DEFAULT_SECTION_BUDGETS)
        configured.update(section_budgets or {})
        self.section_budgets = configured
        self.total_budget = total_budget or int(
            os.getenv("CURIE_CONTEXT_TOKEN_BUDGET", "7600")
        )
        if self.total_budget < 512:
            raise ValueError("Context token budget must be at least 512")
        if any(value < 0 for value in configured.values()):
            raise ValueError("Context section budgets cannot be negative")

    @staticmethod
    def memory_retrieval_allowed(
        memory_policy: str,
        *,
        requires_reference: bool = False,
    ) -> bool:
        """Self-contained operations never pay for unrelated memory retrieval."""

        if memory_policy == "operational_minimal" and not requires_reference:
            return False
        return memory_policy not in {"none", "disabled"}

    def assemble(self, candidates: Iterable[ContextCandidate]) -> ContextEnvelope:
        section_used = {section: 0 for section in ContextSection}
        selected_total = 0
        overflow = 0
        decisions: list[ContextDecision] = []

        for candidate in candidates:
            original_tokens = estimate_tokens(candidate.content)
            if candidate.contradicted:
                decisions.append(
                    ContextDecision(
                        candidate.id,
                        candidate.section,
                        False,
                        "excluded: contradicted by newer explicit information",
                        original_tokens,
                        payload=candidate.payload,
                    )
                )
                continue
            if candidate.rejected:
                decisions.append(
                    ContextDecision(
                        candidate.id,
                        candidate.section,
                        False,
                        "excluded: topic was explicitly rejected",
                        original_tokens,
                        payload=candidate.payload,
                    )
                )
                continue

            never_truncate = candidate.section is ContextSection.CURRENT_MESSAGE or (
                candidate.section is ContextSection.TOOL_RESULT
                and candidate.consequential
            )
            section_budget = self.section_budgets[candidate.section]
            section_remaining = max(0, section_budget - section_used[candidate.section])
            total_remaining = max(0, self.total_budget - selected_total)

            if never_truncate:
                selected = candidate.content
                tokens = original_tokens
                extra = max(0, tokens - total_remaining)
                if candidate.section is not ContextSection.CURRENT_MESSAGE:
                    extra = max(extra, tokens - section_remaining)
                overflow += extra
                reason = (
                    "included in full: current request is never truncated"
                    if candidate.section is ContextSection.CURRENT_MESSAGE
                    else "included in full: consequential tool evidence is atomic"
                )
                truncated = False
            else:
                allowed = min(section_remaining, total_remaining)
                if original_tokens <= allowed:
                    selected = candidate.content
                    tokens = original_tokens
                    reason = f"included: {candidate.reason}"
                    truncated = False
                elif candidate.allow_truncate and allowed >= 24:
                    selected = _truncate_to_tokens(candidate.content, allowed)
                    tokens = estimate_tokens(selected)
                    reason = f"included within budget: {candidate.reason}"
                    truncated = True
                else:
                    decisions.append(
                        ContextDecision(
                            candidate.id,
                            candidate.section,
                            False,
                            "excluded: section or total token budget exhausted",
                            original_tokens,
                            payload=candidate.payload,
                        )
                    )
                    continue

            selected_total += tokens
            section_used[candidate.section] += tokens
            decisions.append(
                ContextDecision(
                    candidate.id,
                    candidate.section,
                    True,
                    reason,
                    tokens,
                    truncated,
                    selected,
                    candidate.payload,
                )
            )

        return ContextEnvelope(
            tuple(decisions),
            self.section_budgets,
            self.total_budget,
            selected_total,
            overflow,
        )
