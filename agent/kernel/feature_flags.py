"""Owner- and connector-scoped feature flags for the typed turn pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Mapping

from agent.kernel.rollout import (
    LegacyRetention,
    RolloutStage,
    effective_rollout_stage,
    read_rollout_state,
)


class PipelineMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    ACTIVE = "active"

    @classmethod
    def parse(
        cls, value: object, default: "PipelineMode" | None = None
    ) -> "PipelineMode":
        fallback = default or cls.ACTIVE
        try:
            return cls(str(value or "").strip().casefold())
        except ValueError:
            return fallback


# Object identity cannot arrive through JSON, Telegram, or another connector.
# Internal tests and operator code may import this capability token explicitly.
PIPELINE_CONTROL_TOKEN = object()


def _csv(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class PipelineFeatureFlags:
    """Resolve rollout mode without requiring a database read on legacy turns."""

    default_mode: PipelineMode = PipelineMode.ACTIVE
    active_owners: frozenset[str] = frozenset()
    shadow_owners: frozenset[str] = frozenset()
    active_connectors: frozenset[str] = frozenset()
    shadow_connectors: frozenset[str] = frozenset()
    rollout_stage: RolloutStage = RolloutStage.DEFAULT_ACTIVE
    smart_home_targets: frozenset[str] = frozenset()
    rollout_state_file: Path | None = None
    legacy_retention: LegacyRetention = LegacyRetention()

    @classmethod
    def from_env(cls) -> "PipelineFeatureFlags":
        return cls(
            default_mode=PipelineMode.parse(
                os.getenv("CURIE_TURN_PIPELINE_MODE", "active")
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
            rollout_stage=RolloutStage.parse(
                os.getenv("CURIE_TURN_PIPELINE_STAGE", "default_active")
            ),
            smart_home_targets=frozenset(
                item.casefold()
                for item in _csv(os.getenv("CURIE_TURN_PIPELINE_DEVICE_CANARY", ""))
            ),
            rollout_state_file=(
                Path(os.environ["CURIE_PIPELINE_ROLLOUT_STATE"]).expanduser()
                if os.getenv("CURIE_PIPELINE_ROLLOUT_STATE", "").strip()
                else None
            ),
            legacy_retention=LegacyRetention(
                owner=os.getenv(
                    "CURIE_LEGACY_PIPELINE_OWNER", "curie-maintainers"
                ).strip()
                or "curie-maintainers",
                removal_deadline=os.getenv(
                    "CURIE_LEGACY_PIPELINE_DEADLINE", "2026-10-22"
                ).strip()
                or "2026-10-22",
            ),
        )

    def decision_for(self, normalized_input: Mapping[str, object]) -> "RolloutDecision":
        state = read_rollout_state(self.rollout_state_file)
        stage = effective_rollout_stage(self.rollout_stage, self.rollout_state_file)
        if state["rollback"]["active"]:
            return RolloutDecision(PipelineMode.LEGACY, stage, "circuit_breaker", False)

        explicit = normalized_input.get("_pipeline_mode")
        authorized = (
            normalized_input.get("_pipeline_control_token") is PIPELINE_CONTROL_TOKEN
        )
        if (
            explicit is not None
            and authorized
            and stage is not RolloutStage.LEGACY_REMOVED
        ):
            return RolloutDecision(
                PipelineMode.parse(explicit, self.default_mode),
                stage,
                "authorized_override",
                False,
            )

        owner = str(normalized_input.get("internal_id") or "")
        connector = str(normalized_input.get("platform") or "").casefold()
        if owner and owner in self.shadow_owners:
            return RolloutDecision(PipelineMode.SHADOW, stage, "owner_shadow", False)
        if connector and connector in self.shadow_connectors:
            return RolloutDecision(
                PipelineMode.SHADOW, stage, "connector_shadow", False
            )

        if stage is RolloutStage.OFFLINE:
            return RolloutDecision(PipelineMode.LEGACY, stage, "offline_only", False)
        if stage is RolloutStage.SHADOW:
            return RolloutDecision(PipelineMode.SHADOW, stage, "shadow_compare", False)

        recognition = None
        try:
            from agent.understanding.recognizers import recognize_deterministic

            recognition = recognize_deterministic(
                str(normalized_input.get("text") or "")
            )
        except Exception:
            recognition = None
        risk = recognition.risk if recognition is not None else "none"
        is_canary_owner = bool(owner and owner in self.active_owners)

        if stage is RolloutStage.CONVERSATION_CANARY:
            active = is_canary_owner and risk in {"none", "read_only"}
            return RolloutDecision(
                PipelineMode.ACTIVE if active else PipelineMode.LEGACY,
                stage,
                "conversation_canary" if active else "outside_conversation_canary",
                False,
            )

        if stage is RolloutStage.SMART_HOME_CANARY:
            if not is_canary_owner:
                return RolloutDecision(
                    PipelineMode.LEGACY, stage, "outside_owner_canary", False
                )
            if risk != "mutating":
                return RolloutDecision(
                    PipelineMode.ACTIVE, stage, "owner_non_mutating", False
                )
            target = (
                str(
                    (recognition.entities if recognition else {}).get("device_target")
                    or ""
                )
                .strip()
                .casefold()
            )
            selected = bool(target and target in self.smart_home_targets)
            return RolloutDecision(
                PipelineMode.ACTIVE if selected else PipelineMode.LEGACY,
                stage,
                "selected_reversible_device" if selected else "mutation_not_selected",
                selected,
            )

        if stage is RolloutStage.CONNECTOR_EXPANSION:
            active = bool(connector and connector in self.active_connectors)
            return RolloutDecision(
                PipelineMode.ACTIVE if active else PipelineMode.SHADOW,
                stage,
                "connector_expansion" if active else "connector_shadow_holdback",
                False,
            )

        if stage is RolloutStage.LEGACY_REMOVED:
            return RolloutDecision(
                PipelineMode.ACTIVE, stage, "legacy_removed", risk == "mutating"
            )
        if owner and owner in self.active_owners:
            return RolloutDecision(PipelineMode.ACTIVE, stage, "owner_active", False)
        if connector and connector in self.active_connectors:
            return RolloutDecision(
                PipelineMode.ACTIVE, stage, "connector_active", False
            )
        return RolloutDecision(
            self.default_mode, stage, "default_active", risk == "mutating"
        )

    def mode_for(self, normalized_input: Mapping[str, object]) -> PipelineMode:
        return self.decision_for(normalized_input).mode

    def status(self) -> dict[str, object]:
        from agent.kernel.rollout import rollout_status

        return rollout_status(
            self.rollout_stage,
            path=self.rollout_state_file,
            retention=self.legacy_retention,
        )


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    mode: PipelineMode
    stage: RolloutStage
    reason: str
    require_verification: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "stage": self.stage.value,
            "reason": self.reason,
            "require_verification": self.require_verification,
        }
