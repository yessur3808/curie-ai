"""Strict canonical smart-home entity resolution."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
import os
import re
from typing import Iterable, Sequence

from services.smart_home.aliases import DeviceAlias, normalize_alias
from services.smart_home.models import CanonicalDevice


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    GROUP = "group"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    REJECTED_ALIAS = "rejected_alias"
    LOW_CONFIDENCE = "low_confidence"


@dataclass(frozen=True, slots=True)
class DeviceResolution:
    status: ResolutionStatus
    devices: tuple[CanonicalDevice, ...]
    confidence: float
    reason: str
    target: str
    candidates: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status in {ResolutionStatus.RESOLVED, ResolutionStatus.GROUP}


_PRONOUNS = {
    "it",
    "that",
    "that one",
    "this",
    "this one",
    "the device",
    "them",
    "those",
    "these",
    "both",
    "both devices",
    "both of them",
    "all of them",
    "those two",
    "these two",
    "previously referenced devices",
}

_DEVICE_TOKEN_CANONICAL = {
    "bulb": "light",
    "bulbs": "light",
    "lamp": "light",
    "lamps": "light",
    "lights": "light",
    "led": "light",
    "leds": "light",
}


def _semantic_tokens(value: str) -> set[str]:
    return {
        _DEVICE_TOKEN_CANONICAL.get(token, token)
        for token in normalize_alias(value).split()
        if token not in {"the", "my", "a", "an", "device"}
    }


def _semantic_name(value: str) -> str:
    return " ".join(
        _DEVICE_TOKEN_CANONICAL.get(token, token)
        for token in normalize_alias(value).split()
    )


def _group_spec(target: str) -> tuple[str, str | None, bool] | None:
    normalized = normalize_alias(target)
    online_only = normalized.startswith("online ")
    normalized = re.sub(
        r"^(?:all|every|each|both|the|my|our|online)\s+", "", normalized
    )
    room: str | None = None
    room_match = re.fullmatch(
        r"(?:lights?|lamps?|bulbs?)\s+(?:in\s+)?(?:the\s+)?(.+)", normalized
    )
    if room_match:
        room = room_match.group(1)
        return "light", room, online_only
    room_match = re.fullmatch(r"(.+?)\s+(?:lights?|lamps?|bulbs?)", normalized)
    if room_match and room_match.group(1) not in {"all", "every", "both", "online"}:
        room = room_match.group(1)
        return "light", room, online_only
    if normalized in {"light", "lights", "lamp", "lamps", "bulb", "bulbs"}:
        return "light", None, online_only
    if normalized in {"switch", "switches"}:
        return "switch", None, online_only
    if normalized in {"controllable device", "controllable devices"}:
        return "controllable", None, online_only
    return None


class DeviceResolver:
    def __init__(
        self,
        *,
        consequential_threshold: float | None = None,
        read_threshold: float | None = None,
        ambiguity_gap: float | None = None,
    ):
        self.consequential_threshold = float(
            consequential_threshold
            if consequential_threshold is not None
            else os.getenv("DEVICE_FUZZY_MUTATION_THRESHOLD", "0.90")
        )
        self.read_threshold = float(
            read_threshold
            if read_threshold is not None
            else os.getenv("DEVICE_FUZZY_READ_THRESHOLD", "0.82")
        )
        self.ambiguity_gap = float(
            ambiguity_gap
            if ambiguity_gap is not None
            else os.getenv("DEVICE_FUZZY_AMBIGUITY_GAP", "0.10")
        )

    def resolve(
        self,
        target: str,
        devices: Sequence[CanonicalDevice],
        *,
        aliases: Iterable[DeviceAlias] = (),
        explicit_provider: str | None = None,
        recent_entity_ids: Sequence[str] = (),
        consequential: bool = True,
    ) -> DeviceResolution:
        query = normalize_alias(target)
        alias_items = tuple(aliases)
        if not query:
            return DeviceResolution(
                ResolutionStatus.NOT_FOUND, (), 0.0, "empty target", target
            )

        # 1. Canonical ID.
        exact = [
            item
            for item in devices
            if item.canonical_id.casefold() == target.strip().casefold()
        ]
        if exact:
            return DeviceResolution(
                ResolutionStatus.RESOLVED, tuple(exact), 1.0, "canonical_id", target
            )

        # 2. Provider ID, only with explicit provider scope.
        if explicit_provider:
            exact = [
                item
                for item in devices
                if item.provider.casefold() == explicit_provider.casefold()
                and item.provider_id.casefold() == target.strip().casefold()
            ]
            if exact:
                return DeviceResolution(
                    ResolutionStatus.RESOLVED, tuple(exact), 1.0, "provider_id", target
                )

        # 3. An explicit rejection is a tombstone for natural names: exact and
        # fuzzy matching must never silently revive it. Canonical or explicitly
        # provider-scoped identifiers above remain available to the owner.
        rejected = [
            item
            for item in alias_items
            if item.status == "rejected" and item.normalized_alias == query
        ]
        if rejected:
            return DeviceResolution(
                ResolutionStatus.REJECTED_ALIAS,
                (),
                1.0,
                "explicitly_rejected_alias",
                target,
            )

        # 4. Exact normalized display name.
        exact = [item for item in devices if item.normalized_name == query]
        if exact:
            return self._unique(exact, target, 1.0, "exact_display_name")

        # 5. Confirmed owner-scoped alias.
        alias_matches: list[CanonicalDevice] = []
        expired_match = False
        for alias in alias_items:
            if alias.status != "confirmed" or alias.normalized_alias != query:
                continue
            if alias.expired:
                expired_match = True
                continue
            device = next(
                (
                    item
                    for item in devices
                    if (alias.device_key and item.canonical_id == alias.device_key)
                    or (
                        alias.provider == item.provider
                        and alias.provider_id == item.provider_id
                    )
                ),
                None,
            )
            if device and device not in alias_matches:
                alias_matches.append(device)
        if alias_matches:
            return self._unique(alias_matches, target, 0.99, "confirmed_owner_alias")
        if expired_match and consequential:
            return DeviceResolution(
                ResolutionStatus.LOW_CONFIDENCE,
                (),
                0.69,
                "expired_alias_requires_confirmation",
                target,
            )

        # 6. Explicit room/type and capability groups.
        if spec := _group_spec(target):
            capability, room, online_only = spec
            grouped = [
                item
                for item in devices
                if capability in item.capabilities
                and (not room or normalize_alias(item.room or "") == room)
                and (not online_only or item.online_status is True)
            ]
            if grouped:
                return DeviceResolution(
                    ResolutionStatus.GROUP,
                    tuple(grouped),
                    1.0,
                    "capability_group",
                    target,
                )

        query_tokens = _semantic_tokens(query)
        token_matches = [
            item
            for item in devices
            if query_tokens and query_tokens <= _semantic_tokens(item.normalized_name)
        ]
        if len(token_matches) > 1:
            return DeviceResolution(
                ResolutionStatus.AMBIGUOUS,
                (),
                0.91,
                "shared_display_name_tokens",
                target,
                tuple(item.display_name for item in token_matches),
            )
        if len(token_matches) == 1:
            return DeviceResolution(
                ResolutionStatus.RESOLVED,
                tuple(token_matches),
                0.93,
                "unique_display_name_tokens",
                target,
            )

        # 7. Strong fuzzy display-name match. Aliases and product families are
        # intentionally excluded from fuzzy matching.
        ranked = sorted(
            (
                (
                    max(
                        SequenceMatcher(
                            None,
                            _semantic_name(query).replace(" ", ""),
                            candidate.replace(" ", ""),
                        ).ratio()
                        for candidate in (
                            item.normalized_name,
                            _semantic_name(item.normalized_name),
                            re.sub(r"\s+\d+$", "", item.normalized_name),
                            *(
                                token
                                for token in item.normalized_name.split()
                                if len(token) >= 4
                            ),
                        )
                    ),
                    item,
                )
                for item in devices
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        threshold = (
            self.consequential_threshold if consequential else self.read_threshold
        )
        if ranked and ranked[0][0] >= threshold:
            if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < self.ambiguity_gap:
                return DeviceResolution(
                    ResolutionStatus.AMBIGUOUS,
                    (),
                    ranked[0][0],
                    "fuzzy_candidates_too_close",
                    target,
                    tuple(item.display_name for _, item in ranked[:5]),
                )
            return DeviceResolution(
                ResolutionStatus.RESOLVED,
                (ranked[0][1],),
                ranked[0][0],
                "strong_fuzzy_name",
                target,
            )

        # 8. Recent dialogue-state entity reference.
        if query in _PRONOUNS and recent_entity_ids:
            recent = [
                item for item in devices if item.canonical_id in set(recent_entity_ids)
            ]
            if recent:
                wanted_plural = query in {
                    "them",
                    "those",
                    "these",
                    "both",
                    "both devices",
                    "both of them",
                    "all of them",
                    "those two",
                    "these two",
                    "previously referenced devices",
                }
                selected = recent if wanted_plural else recent[:1]
                return DeviceResolution(
                    ResolutionStatus.RESOLVED,
                    tuple(selected),
                    0.98,
                    "dialogue_state_reference",
                    target,
                )

        status = (
            ResolutionStatus.LOW_CONFIDENCE
            if ranked and ranked[0][0] >= 0.6
            else ResolutionStatus.NOT_FOUND
        )
        candidates = tuple(item.display_name for _, item in ranked[:5])
        confidence = ranked[0][0] if ranked else 0.0
        return DeviceResolution(
            status, (), confidence, "clarification_required", target, candidates
        )

    @staticmethod
    def _unique(
        devices: Sequence[CanonicalDevice],
        target: str,
        confidence: float,
        reason: str,
    ) -> DeviceResolution:
        if len(devices) == 1:
            return DeviceResolution(
                ResolutionStatus.RESOLVED, tuple(devices), confidence, reason, target
            )
        return DeviceResolution(
            ResolutionStatus.AMBIGUOUS,
            (),
            confidence,
            f"ambiguous_{reason}",
            target,
            tuple(item.display_name for item in devices),
        )
