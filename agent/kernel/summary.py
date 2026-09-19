"""Structured rolling-summary metadata and deterministic correction handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping

_CORRECTION_PATTERNS = (
    re.compile(
        r"\bthere\s+(?:is|are)\s+no\s+(?:device\s+called\s+)?"
        r"(?P<target>[A-Za-z0-9][A-Za-z0-9 '\-_]{0,80})",
        re.I,
    ),
    re.compile(
        r"\b(?:that(?:'s| is)\s+wrong|no)[,. ]+(?:i\s+meant\s+)?"
        r"(?P<target>[A-Za-z0-9][A-Za-z0-9 '\-_]{0,80})",
        re.I,
    ),
    re.compile(
        r"\bi\s+(?:actually\s+)?meant\s+"
        r"(?P<target>[A-Za-z0-9][A-Za-z0-9 '\-_]{0,80})",
        re.I,
    ),
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class ExplicitCorrection:
    text: str
    target: str
    source_fingerprint: str

    def as_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "target": self.target,
            "source_fingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class RollingSummaryRecord:
    summary: str
    covered_start_fingerprint: str
    covered_tail_fingerprint: str
    covered_turn_count: int
    corrections: tuple[ExplicitCorrection, ...] = ()
    generated_inference: bool = True
    updated_at: str = ""

    def as_metadata(self) -> dict[str, Any]:
        return {
            # Keep the established key version for storage compatibility while
            # publishing the richer record schema explicitly.
            "version": 1,
            "record_schema": 2,
            "summary": self.summary,
            "covered_start_fingerprint": self.covered_start_fingerprint,
            "covered_tail_fingerprint": self.covered_tail_fingerprint,
            "covered_turn_count": self.covered_turn_count,
            "source_turn_range": {
                "start": self.covered_start_fingerprint,
                "end": self.covered_tail_fingerprint,
                "count": self.covered_turn_count,
            },
            "corrections": [item.as_dict() for item in self.corrections],
            "generated_inference": self.generated_inference,
            "updated_at": self.updated_at or datetime.now(timezone.utc).isoformat(),
        }


def extract_explicit_corrections(
    history: Iterable[tuple[str, str]], fingerprint
) -> tuple[ExplicitCorrection, ...]:
    """Extract only unmistakable user corrections, never assistant guesses."""

    corrections: list[ExplicitCorrection] = []
    for entry in history:
        role, content = entry
        if str(role).casefold() != "user":
            continue
        clean = " ".join(str(content).split()).strip()
        for pattern in _CORRECTION_PATTERNS:
            match = pattern.search(clean)
            if not match:
                continue
            target = match.group("target").strip(" .,!?:;")
            if target:
                corrections.append(
                    ExplicitCorrection(clean[:240], target, fingerprint(entry))
                )
            break
    # Most recent statement wins when the same target is corrected repeatedly.
    unique: dict[str, ExplicitCorrection] = {}
    for item in corrections:
        unique[item.target.casefold()] = item
    return tuple(unique.values())


def rewrite_summary_conflicts(
    summary: str, corrections: Iterable[ExplicitCorrection]
) -> str:
    """Remove conflicting inferred sentences and append authoritative corrections."""

    corrections = tuple(corrections)
    if not corrections:
        return summary.strip()
    targets = {item.target.casefold() for item in corrections}
    sentences = _SENTENCE_BOUNDARY.split(summary.strip()) if summary.strip() else []
    retained = [
        sentence
        for sentence in sentences
        if not any(target in sentence.casefold() for target in targets)
    ]
    authoritative = [f"Explicit correction: {item.text}" for item in corrections]
    return " ".join([*retained, *authoritative]).strip()


def summary_prompt_guard(corrections: Iterable[ExplicitCorrection]) -> str:
    corrections = tuple(corrections)
    if not corrections:
        return ""
    rendered = "\n".join(f"- {item.text}" for item in corrections)
    return (
        "\nExplicit user corrections (authoritative; rewrite or invalidate any "
        f"conflicting earlier claim):\n{rendered}\n"
    )


def corrections_from_metadata(
    value: Mapping[str, Any] | None,
) -> tuple[ExplicitCorrection, ...]:
    results: list[ExplicitCorrection] = []
    for item in (value or {}).get("corrections", ()):
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        target = str(item.get("target") or "").strip()
        source = str(item.get("source_fingerprint") or "").strip()
        if text and target:
            results.append(ExplicitCorrection(text, target, source))
    return tuple(results)
