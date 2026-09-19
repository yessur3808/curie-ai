"""Conservative compound-request decomposition with explicit dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Callable


class ClauseRelation(str, Enum):
    INDEPENDENT = "independent"
    REQUESTED_ORDER = "requested_order"
    DATA_DEPENDENCY = "data_dependency"


@dataclass(frozen=True, slots=True)
class CompoundClause:
    id: str
    text: str
    start: int
    end: int
    relation: ClauseRelation
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompoundRequest:
    original: str
    clauses: tuple[CompoundClause, ...]
    preserve_order: bool
    ambiguous: bool = False
    clarification_reason: str | None = None

    @property
    def decomposed(self) -> bool:
        return len(self.clauses) > 1


_SEQUENCE = re.compile(
    r"\s*(;|\band then\b|\bthen also\b|\bafter that\b|\bthen\b)\s*", re.I
)
_INDEPENDENT = re.compile(r"\s+\band\b\s+", re.I)
_REFERENCE = re.compile(
    r"\b(?:it|that|them|those|the result|the file|the message)\b", re.I
)


def _parts(pattern: re.Pattern[str], text: str) -> list[tuple[str, int, int]]:
    output: list[tuple[str, int, int]] = []
    cursor = 0
    for match in pattern.finditer(text):
        piece = text[cursor : match.start()].strip()
        if piece:
            start = text.find(piece, cursor, match.start())
            output.append((piece, start, start + len(piece)))
        cursor = match.end()
    piece = text[cursor:].strip()
    if piece:
        start = text.find(piece, cursor)
        output.append((piece, start, start + len(piece)))
    return output


def decompose_request(
    text: str,
    *,
    independently_meaningful: Callable[[str], bool] | None = None,
) -> CompoundRequest:
    """Split only when every clause can stand on its own.

    Sequence markers always preserve order.  Plain ``and`` is accepted only when
    the caller proves each side is independently meaningful; this avoids splitting
    device lists and ordinary prose.
    """
    original = str(text or "").strip()
    if not original:
        return CompoundRequest(original, ())
    meaningful = independently_meaningful or (lambda value: bool(value.strip()))
    sequential = _parts(_SEQUENCE, original)
    relation = ClauseRelation.REQUESTED_ORDER
    preserve_order = True
    candidates = sequential
    if len(sequential) <= 1:
        candidates = _parts(_INDEPENDENT, original)
        relation = ClauseRelation.INDEPENDENT
        preserve_order = False
    if not 1 < len(candidates) <= 8 or not all(
        meaningful(item[0]) for item in candidates
    ):
        return CompoundRequest(
            original,
            (
                CompoundClause(
                    "step-1", original, 0, len(original), ClauseRelation.INDEPENDENT
                ),
            ),
            True,
        )
    clauses: list[CompoundClause] = []
    previous: str | None = None
    for index, (clause_text, start, end) in enumerate(candidates, 1):
        identifier = f"step-{index}"
        clause_relation = relation
        dependencies: tuple[str, ...] = ()
        if previous and (preserve_order or _REFERENCE.search(clause_text)):
            dependencies = (previous,)
            if _REFERENCE.search(clause_text):
                clause_relation = ClauseRelation.DATA_DEPENDENCY
                preserve_order = True
        clauses.append(
            CompoundClause(
                identifier, clause_text, start, end, clause_relation, dependencies
            )
        )
        previous = identifier
    return CompoundRequest(original, tuple(clauses), preserve_order)
