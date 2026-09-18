"""Owner- and connector-scoped feature flags for the typed turn pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping


class PipelineMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    ACTIVE = "active"

    @classmethod
    def parse(
        cls, value: object, default: "PipelineMode" | None = None
    ) -> "PipelineMode":
        fallback = default or cls.LEGACY
        try:
            return cls(str(value or "").strip().casefold())
        except ValueError:
            return fallback


def _csv(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class PipelineFeatureFlags:
    """Resolve rollout mode without requiring a database read on legacy turns."""

    default_mode: PipelineMode = PipelineMode.LEGACY
    active_owners: frozenset[str] = frozenset()
    shadow_owners: frozenset[str] = frozenset()
    active_connectors: frozenset[str] = frozenset()
    shadow_connectors: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> "PipelineFeatureFlags":
        return cls(
            default_mode=PipelineMode.parse(
                os.getenv("CURIE_TURN_PIPELINE_MODE", "legacy")
            ),
            active_owners=_csv(os.getenv("CURIE_TURN_PIPELINE_ACTIVE_OWNERS", "")),
            shadow_owners=_csv(os.getenv("CURIE_TURN_PIPELINE_SHADOW_OWNERS", "")),
            active_connectors=frozenset(
                item.casefold()
                for item in _csv(os.getenv("CURIE_TURN_PIPELINE_ACTIVE_CONNECTORS", ""))
            ),
            shadow_connectors=frozenset(
                item.casefold()
                for item in _csv(os.getenv("CURIE_TURN_PIPELINE_SHADOW_CONNECTORS", ""))
            ),
        )

    def mode_for(self, normalized_input: Mapping[str, object]) -> PipelineMode:
        explicit = normalized_input.get("_pipeline_mode")
        if explicit is not None:
            return PipelineMode.parse(explicit, self.default_mode)

        owner = str(normalized_input.get("internal_id") or "")
        connector = str(normalized_input.get("platform") or "").casefold()
        if owner and owner in self.active_owners:
            return PipelineMode.ACTIVE
        if owner and owner in self.shadow_owners:
            return PipelineMode.SHADOW
        if connector and connector in self.active_connectors:
            return PipelineMode.ACTIVE
        if connector and connector in self.shadow_connectors:
            return PipelineMode.SHADOW
        return self.default_mode
