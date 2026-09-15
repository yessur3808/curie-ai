"""Bounded dialogue and reference state, scoped to one owner and connector."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import re
import threading
import time
from typing import Any, Iterable, Mapping

from agent.intent_router import classify_request
from .contracts import EntityReference, TurnState

_SINGULAR_REFERENCE = re.compile(
    r"\b(?:it|that one|this one|that device|this device|the device)\b", re.I
)
_PLURAL_REFERENCE = re.compile(
    r"\b(?:them|those devices|these devices|both devices|both)\b", re.I
)
_REFERENCE_CONTEXT = re.compile(
    r"(?:\b(?:turn|switch|power)\b.*\b(?:on|off)\b)|"
    r"(?:\b(?:is|are)\b.*\b(?:on|off|online|offline|running)\b)|"
    r"(?:\b(?:status|check|retry|try again|still on|still off)\b)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    original_text: str
    resolved_text: str
    entities: tuple[EntityReference, ...] = ()
    used_context: bool = False


@dataclass(slots=True)
class DialogueContext:
    entities: tuple[EntityReference, ...] = ()
    last_capability: str | None = None
    active_goal_id: str | None = None
    pending_clarification: str | None = None
    updated_at: float = field(default_factory=time.monotonic)


def _history_targets(history: Iterable[Any] | None) -> list[str]:
    for item in reversed(list(history or [])[-8:]):
        if isinstance(item, Mapping):
            role, content = item.get("role"), item.get("content")
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role, content = item[0], item[1]
        else:
            continue
        if str(role).casefold() != "user" or not isinstance(content, str):
            continue
        request = classify_request(content)
        if not request or request.action not in {"home_control", "home_status"}:
            continue
        targets = [str(value) for value in request.params.get("targets", ())]
        target = str(request.params.get("target") or "").strip()
        if target:
            targets.insert(0, target)
        if targets:
            return targets
    return []


def _result_entities(result: Mapping[str, Any]) -> tuple[EntityReference, ...]:
    """Use verified tool identities instead of retaining a vague group phrase."""
    raw_names = result.get("_dialogue_entities") or ()
    if isinstance(raw_names, (str, bytes)):
        raw_names = (raw_names,)
    names: list[str] = []
    for value in raw_names if isinstance(raw_names, Iterable) else ():
        name = str(value or "").strip()
        if name and name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
        if len(names) >= 32:
            break
    return tuple(
        EntityReference(
            surface=name,
            resolved_name=name,
            source="tool_result",
            confidence=1.0,
        )
        for name in names
    )


class DialogueStateStore:
    """Small in-memory state for references; durable memory remains elsewhere."""

    def __init__(self, *, ttl_seconds: int = 1800, max_sessions: int = 1000):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_sessions = max(1, int(max_sessions))
        self._items: OrderedDict[str, DialogueContext] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(platform: str, owner_id: str) -> str:
        return f"{platform.casefold()}:{owner_id}"

    def _get(self, platform: str, owner_id: str) -> DialogueContext | None:
        key = self._key(platform, owner_id)
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item and now - item.updated_at > self.ttl_seconds:
                self._items.pop(key, None)
                item = None
            if item:
                self._items.move_to_end(key)
            return item

    def resolve_references(
        self,
        text: str,
        *,
        platform: str,
        owner_id: str,
        history: Iterable[Any] | None = None,
    ) -> ReferenceResolution:
        clean = str(text or "").strip()
        if not clean or not _REFERENCE_CONTEXT.search(clean):
            return ReferenceResolution(clean, clean)
        singular = _SINGULAR_REFERENCE.search(clean)
        plural = _PLURAL_REFERENCE.search(clean)
        if not singular and not plural:
            return ReferenceResolution(clean, clean)

        context = self._get(platform, owner_id)
        names = [item.resolved_name for item in context.entities] if context else []
        if not names:
            names = _history_targets(history)
        if plural and len(names) < 2:
            return ReferenceResolution(clean, clean)
        if singular and not names:
            return ReferenceResolution(clean, clean)

        selected = names if plural else names[:1]
        replacement = " and ".join(selected)
        pattern = _PLURAL_REFERENCE if plural else _SINGULAR_REFERENCE
        resolved = pattern.sub(replacement, clean, count=1)
        entities = tuple(
            EntityReference(
                surface=(plural or singular).group(0),
                resolved_name=name,
                source="dialogue_state" if context and context.entities else "history",
                confidence=0.96 if context and context.entities else 0.9,
            )
            for name in selected
        )
        return ReferenceResolution(clean, resolved, entities, True)

    @staticmethod
    def _observed_context(
        state: TurnState,
        result: Mapping[str, Any],
        previous: DialogueContext | None = None,
    ) -> DialogueContext:
        capabilities = [
            item.capability for item in state.goal.subgoals if item.capability
        ]
        pending = (
            "awaiting_user_detail"
            if state.response_mode.value == "clarification"
            else None
        )
        result_entities = _result_entities(result)
        return DialogueContext(
            entities=result_entities
            or state.entities
            or (previous.entities if previous else ()),
            last_capability=(
                capabilities[-1]
                if capabilities
                else (previous.last_capability if previous else None)
            ),
            active_goal_id=state.goal.id,
            pending_clarification=pending,
        )

    def observe(self, state: TurnState, result: Mapping[str, Any]) -> None:
        key = self._key(state.platform, state.owner_scope_hash)
        # TurnState contains only the owner hash. Store under that hash too so a
        # caller can use an already-scoped owner without retaining its raw ID.
        with self._lock:
            context = self._observed_context(state, result, self._items.get(key))
            self._items[key] = context
            self._items.move_to_end(key)
            while len(self._items) > self.max_sessions:
                self._items.popitem(last=False)

    def observe_for_owner(
        self, owner_id: str, state: TurnState, result: Mapping[str, Any]
    ) -> None:
        key = self._key(state.platform, str(owner_id))
        hashed_key = self._key(state.platform, state.owner_scope_hash)
        with self._lock:
            previous = self._items.get(key) or self._items.get(hashed_key)
            context = self._observed_context(state, result, previous)
            self._items[hashed_key] = context
            self._items[key] = context
            self._items.move_to_end(hashed_key)
            self._items.move_to_end(key)
            while len(self._items) > self.max_sessions:
                self._items.popitem(last=False)

    def clear(self, *, platform: str, owner_id: str) -> None:
        with self._lock:
            self._items.pop(self._key(platform, owner_id), None)
