"""Human-calibrated scoring for qualities that deterministic rules cannot judge."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable, Mapping


_SUBJECTIVE_DIMENSIONS = {
    "naturalness",
    "warmth",
    "personality_consistency",
    "conversational_directness",
}


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    sample_count: int
    mean_absolute_error: float
    within_one_agreement: float
    calibrated: bool

    def as_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "mean_absolute_error": self.mean_absolute_error,
            "within_one_agreement": self.within_one_agreement,
            "calibrated": self.calibrated,
        }


class CalibratedSubjectiveGrader:
    """Gate subjective scores on a minimum human-labelled calibration set."""

    def __init__(self, *, minimum_samples: int = 20, maximum_mae: float = 0.6):
        self.minimum_samples = int(minimum_samples)
        self.maximum_mae = float(maximum_mae)
        self.calibration: CalibrationResult | None = None

    def calibrate(self, pairs: Iterable[tuple[float, float]]) -> CalibrationResult:
        pairs = [(float(human), float(model)) for human, model in pairs]
        errors = [abs(human - model) for human, model in pairs]
        result = CalibrationResult(
            sample_count=len(pairs),
            mean_absolute_error=round(mean(errors), 4) if errors else 5.0,
            within_one_agreement=(
                round(sum(error <= 1.0 for error in errors) / len(errors), 4)
                if errors
                else 0.0
            ),
            calibrated=bool(pairs)
            and len(pairs) >= self.minimum_samples
            and mean(errors) <= self.maximum_mae,
        )
        self.calibration = result
        return result

    def accept(self, dimension: str, score: float, *, threshold: float) -> bool:
        if dimension not in _SUBJECTIVE_DIMENSIONS:
            raise ValueError(
                "subjective graders cannot decide correctness, security, or safety gates"
            )
        if self.calibration is None or not self.calibration.calibrated:
            raise RuntimeError(
                "subjective grader must be calibrated against human labels"
            )
        return float(score) >= float(threshold)

    def describe(self) -> Mapping[str, object]:
        return {
            "allowed_dimensions": sorted(_SUBJECTIVE_DIMENSIONS),
            "calibration": self.calibration.as_dict() if self.calibration else None,
        }


__all__ = ["CalibrationResult", "CalibratedSubjectiveGrader"]
