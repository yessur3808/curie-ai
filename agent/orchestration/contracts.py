"""Shared response contract for conversation handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ResponseCandidate:
    text: str
    model_used: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
