"""Typed, user-visible provenance metadata for assistant responses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypedDict

SourceKind = Literal[
    "deterministic",
    "live_tool",
    "document",
    "user_memory",
    "model_knowledge",
    "inference",
]


class ResponseProvenance(TypedDict):
    kind: SourceKind
    confidence: float
    observed_at: str
    detail: str


def response_provenance(
    *, model_used: str = "", user_text: str = "", response_text: str = ""
) -> ResponseProvenance:
    combined = f"{user_text}\n{response_text}".casefold()
    model = model_used.casefold()
    if model.startswith("deterministic"):
        kind, confidence, detail = "deterministic", 1.0, "Verified local calculation"
    elif any(
        token in model for token in ("weather", "research", "browser", "navigation")
    ):
        kind, confidence, detail = "live_tool", 0.95, "Current tool result"
    elif any(
        marker in combined
        for marker in (
            "[untrusted attachment",
            "[local vision result",
            "[voice note transcript",
        )
    ):
        kind, confidence, detail = "document", 0.9, "User-provided media"
    elif "memory" in model:
        kind, confidence, detail = "user_memory", 0.9, "Stored user information"
    else:
        kind, confidence, detail = "model_knowledge", 0.65, "Language-model knowledge"
    return {
        "kind": kind,
        "confidence": confidence,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
