"""Owner-scoped dialogue state, transition rules, and grounded references."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import re
import threading
import time
from typing import Any, Iterable, Mapping

from agent.intent_router import classify_request
from .contracts import EntityReference, TurnState


class TransitionClass(str, Enum):
    CONTINUATION = "continuation"
    DIRECT_ANSWER = "direct_answer"
    CORRECTION = "correction"
    REJECTION = "rejection"
    NEW_GOAL = "new_goal"
    INTERRUPTION = "interruption"
    CANCELLATION = "cancellation"
    APPROVAL = "approval"
    DENIAL = "denial"
    UNRELATED_SMALL_TALK = "unrelated_small_talk"
    CONNECTOR_COMMAND = "connector_command"


@dataclass(frozen=True, slots=True)
class DialogueTransition:
    kind: TransitionClass
    reason: str
    confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    original_text: str
    resolved_text: str
    entities: tuple[EntityReference, ...] = ()
    used_context: bool = False


@dataclass(frozen=True, slots=True)
class StateValue:
    """One dialogue fact with mandatory provenance and expiry metadata."""

    value: Any
    source_turn: str
    created_at: float
    expires_at: float | None
    confidence: float
    owner_scope: str
    source: str = "turn"

    def __post_init__(self) -> None:
        if not self.source_turn.strip():
            raise ValueError("Dialogue state values require a source turn")
        if not self.owner_scope.strip():
            raise ValueError("Dialogue state values require an owner scope")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Dialogue state confidence must be between zero and one")

    def expired(self, now: float | None = None) -> bool:
        return self.expires_at is not None and (now or time.time()) >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        value = self.value
        if isinstance(value, tuple) and all(
            isinstance(item, EntityReference) for item in value
        ):
            value = [item.as_dict() for item in value]
        return {
            "value": value,
            "source_turn": self.source_turn,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "confidence": self.confidence,
            "owner_scope": self.owner_scope,
            "source": self.source,
        }


@dataclass(slots=True)
class DialogueState:
    """Explicit current conversational situation for one owner and connector."""

    active_goal: StateValue | None = None
    active_topic: StateValue | None = None
    last_completed_goal: StateValue | None = None
    last_failed_goal: StateValue | None = None
    unresolved_assistant_question: StateValue | None = None
    expected_answer_type: StateValue | None = None
    referenced_entities: StateValue | None = None
    last_verified_tool_result: StateValue | None = None
    pending_approval: StateValue | None = None
    pending_task: StateValue | None = None
    topic_rejections: StateValue | None = None
    last_proactive_message: StateValue | None = None
    proactive_answered: StateValue | None = None
    updated_at: float = field(default_factory=time.time)

    @property
    def entities(self) -> tuple[EntityReference, ...]:
        slot = self.referenced_entities
        if slot is None or slot.expired():
            return ()
        value = slot.value
        return tuple(item for item in value if isinstance(item, EntityReference))

    @property
    def active_goal_id(self) -> str | None:
        if not self.active_goal or self.active_goal.expired():
            return None
        value = self.active_goal.value
        return str(value.get("id")) if isinstance(value, Mapping) else str(value)

    @property
    def pending_clarification(self) -> str | None:
        if not self.unresolved_assistant_question:
            return None
        return str(self.unresolved_assistant_question.value)

    def pruned(self, now: float | None = None) -> "DialogueState":
        now = now or time.time()
        values: dict[str, Any] = {"updated_at": self.updated_at}
        for name in _STATE_FIELDS:
            slot = getattr(self, name)
            values[name] = None if slot and slot.expired(now) else slot
        return DialogueState(**values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            **{
                name: getattr(self, name).as_dict() if getattr(self, name) else None
                for name in _STATE_FIELDS
            },
            "updated_at": self.updated_at,
        }


# Backward-compatible name retained for modules that imported the Phase 1 type.
DialogueContext = DialogueState

_STATE_FIELDS = (
    "active_goal",
    "active_topic",
    "last_completed_goal",
    "last_failed_goal",
    "unresolved_assistant_question",
    "expected_answer_type",
    "referenced_entities",
    "last_verified_tool_result",
    "pending_approval",
    "pending_task",
    "topic_rejections",
    "last_proactive_message",
    "proactive_answered",
)

_SINGULAR_REFERENCE = re.compile(
    r"\b(?:it|that one|this one|that device|this device|the device)\b", re.I
)
_PLURAL_REFERENCE = re.compile(
    r"\b(?:both\s+of\s+them|all\s+of\s+them|those\s+two|these\s+two|"
    r"them|those\s+devices|these\s+devices|both\s+devices|both)\b",
    re.I,
)
_REFERENCE_CONTEXT = re.compile(
    r"(?:\b(?:turn|switch|power)\b.*\b(?:on|off)\b)|"
    r"(?:\b(?:stay|remain)\b.*\b(?:on|off)\b)|"
    r"(?:\b(?:is|are)\b.*\b(?:on|off|online|offline|running)\b)|"
    r"(?:\b(?:status|check|retry|try again|still on|still off)\b)",
    re.I,
)
_CORRECTION = re.compile(
    r"(?:\b(?:no|actually|correction)\b.{0,24}\b(?:meant|called|is|are)\b|"
    r"\bthere\s+(?:is|are)\s+no\b|\bthat(?:'s| is)\s+(?:wrong|incorrect)\b|"
    r"\bi\s+(?:actually\s+)?meant\b)",
    re.I,
)
_CORRECTED_DEVICE = re.compile(
    r"\bthere\s+(?:is|are)\s+no\s+(?:device\s+called\s+)?"
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9 '\-_]{0,80})",
    re.I,
)
_REJECTION = re.compile(
    r"\b(?:stop talking about|do not (?:mention|bring up)|don't (?:mention|bring up)|"
    r"not interested in|drop the (?:topic of )?|leave .* alone)\b",
    re.I,
)
_REJECTED_TOPIC = re.compile(
    r"\b(?:stop talking about|do not (?:mention|bring up)|don't (?:mention|bring up)|"
    r"not interested in|drop the (?:topic of )?)\s+(?P<topic>.+?)[.!?]*$",
    re.I,
)
_CANCELLATION = re.compile(
    r"^\s*(?:cancel(?: that| it)?|never ?mind|stop(?: that| it)?|forget it)\s*[.!?]*$",
    re.I,
)
_APPROVAL = re.compile(
    r"^\s*(?:yes|yep|yeah|sure|approved?|go ahead|do it|please do)\s*[.!?]*$",
    re.I,
)
_DENIAL = re.compile(r"^\s*(?:no|nope|deny|denied|do not|don't|cancel)\s*[.!?]*$", re.I)
_SMALL_TALK = re.compile(
    r"^\s*(?:hi|hello|hey|bonjour|thanks?|thank you|good (?:morning|night)|"
    r"how are you|lol|haha|nice|cool)[!,.? ]*$",
    re.I,
)
_EXPLICIT_REQUEST = re.compile(
    r"(?:^\s*(?:please\s+)?(?:turn|switch|show|tell|find|search|check|create|"
    r"write|send|delete|cancel|schedule|remind|explain|compare|analy[sz]e|help|"
    r"make|build|fix|list|open|close|start|stop|set|change|update)\b|"
    r"\b(?:can|could|would|will)\s+you\b|\?$)",
    re.I,
)
_CONTINUATION = re.compile(
    r"^\s*(?:and|also|then|what about|how about|as for|about that|try again)\b",
    re.I,
)
_STOP_WORDS = frozenset(
    "a an and are as at be both by can could do for from how i in is it me my of "
    "on or please that the them these this those to turn was what when where which "
    "who why will with would you your".split()
)


def _owner_scope(owner_id: str) -> str:
    return hashlib.sha256(str(owner_id).encode()).hexdigest()[:16]


def _slot(
    value: Any,
    *,
    source_turn: str,
    owner_scope: str,
    ttl: float | None,
    confidence: float = 1.0,
    source: str = "turn",
    now: float | None = None,
) -> StateValue:
    now = now or time.time()
    return StateValue(
        value,
        source_turn,
        now,
        now + ttl if ttl is not None else None,
        confidence,
        owner_scope,
        source,
    )


def _slot_value(slot: StateValue | None, default: Any = None) -> Any:
    return default if slot is None or slot.expired() else slot.value


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
            return list(dict.fromkeys(targets))
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
        EntityReference(name, name, source="tool_result", confidence=1.0)
        for name in names
    )


def _topic_from_text(text: str) -> str | None:
    match = _REJECTED_TOPIC.search(text)
    if match:
        topic = match.group("topic").strip(" .,!?:;").casefold()
        return re.sub(r"^(?:the|a|an)\s+", "", topic)[:120]
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]*", text.casefold())
        if word not in _STOP_WORDS and len(word) > 1
    ]
    return " ".join(words[:5]) or None


def _corrected_device(text: str) -> str | None:
    match = _CORRECTED_DEVICE.search(text)
    return match.group("name").strip(" .,!?:;") if match else None


def classify_transition(
    text: str, state: DialogueState | None = None
) -> DialogueTransition:
    """Classify a turn using explicit precedence instead of fuzzy history."""

    clean = " ".join(str(text or "").split()).strip()
    current = (state or DialogueState()).pruned()
    if clean.startswith("/"):
        return DialogueTransition(
            TransitionClass.CONNECTOR_COMMAND, "explicit connector command"
        )
    if _CORRECTION.search(clean):
        return DialogueTransition(
            TransitionClass.CORRECTION,
            "explicit correction outranks stored dialogue and memory",
        )
    if _REJECTION.search(clean):
        return DialogueTransition(TransitionClass.REJECTION, "explicit topic rejection")
    if _CANCELLATION.fullmatch(clean):
        return DialogueTransition(TransitionClass.CANCELLATION, "explicit cancellation")

    pending_approval = _slot_value(current.pending_approval)
    if pending_approval and _APPROVAL.fullmatch(clean):
        return DialogueTransition(
            TransitionClass.APPROVAL, "answer to the pending approval"
        )
    if pending_approval and _DENIAL.fullmatch(clean):
        return DialogueTransition(
            TransitionClass.DENIAL, "denial of the pending approval"
        )

    has_reference = bool(
        _SINGULAR_REFERENCE.search(clean) or _PLURAL_REFERENCE.search(clean)
    )
    if has_reference and current.entities and _REFERENCE_CONTEXT.search(clean):
        return DialogueTransition(
            TransitionClass.CONTINUATION,
            "device reference uses the latest relevant entity set",
        )
    if _CONTINUATION.search(clean):
        return DialogueTransition(
            TransitionClass.CONTINUATION, "explicit continuation wording"
        )

    if _EXPLICIT_REQUEST.search(clean):
        if _slot_value(current.pending_task):
            return DialogueTransition(
                TransitionClass.INTERRUPTION,
                "fresh explicit request interrupts the pending task context",
            )
        return DialogueTransition(
            TransitionClass.NEW_GOAL,
            "fresh explicit request outranks earlier and proactive context",
        )

    unresolved = _slot_value(current.unresolved_assistant_question)
    if unresolved and clean:
        return DialogueTransition(
            TransitionClass.DIRECT_ANSWER,
            "response to the unresolved assistant question",
            0.9,
        )
    if _SMALL_TALK.fullmatch(clean):
        return DialogueTransition(
            TransitionClass.UNRELATED_SMALL_TALK,
            "self-contained social turn does not inherit the active topic",
        )
    if _slot_value(current.active_goal):
        return DialogueTransition(
            TransitionClass.CONTINUATION,
            "active unresolved goal remains relevant",
            0.75,
        )
    return DialogueTransition(
        TransitionClass.NEW_GOAL, "new self-contained conversational turn", 0.75
    )


class DialogueStateStore:
    """Bounded explicit dialogue state; durable memories remain a separate layer."""

    def __init__(self, *, ttl_seconds: int = 7200, max_sessions: int = 1000):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_sessions = max(1, int(max_sessions))
        self._items: OrderedDict[str, DialogueState] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(platform: str, owner_id: str) -> str:
        return f"{platform.casefold()}:{owner_id}"

    def _get(self, platform: str, owner_id: str) -> DialogueState | None:
        key = self._key(platform, owner_id)
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if item and now - item.updated_at > self.ttl_seconds:
                self._items.pop(key, None)
                item = None
            if item:
                item = item.pruned(now)
                self._items[key] = item
                self._items.move_to_end(key)
            return item

    def snapshot(self, *, platform: str, owner_id: str) -> DialogueState:
        return self._get(platform, owner_id) or DialogueState()

    def transition_for(
        self, text: str, *, platform: str, owner_id: str
    ) -> DialogueTransition:
        return classify_transition(text, self._get(platform, owner_id))

    def resolve_references(
        self,
        text: str,
        *,
        platform: str,
        owner_id: str,
        history: Iterable[Any] | None = None,
        transition: DialogueTransition | None = None,
    ) -> ReferenceResolution:
        clean = str(text or "").strip()
        transition = transition or self.transition_for(
            clean, platform=platform, owner_id=owner_id
        )
        if transition.kind in {
            TransitionClass.CORRECTION,
            TransitionClass.REJECTION,
            TransitionClass.CANCELLATION,
        }:
            return ReferenceResolution(clean, clean)
        if not clean or not _REFERENCE_CONTEXT.search(clean):
            return ReferenceResolution(clean, clean)
        singular = _SINGULAR_REFERENCE.search(clean)
        plural = _PLURAL_REFERENCE.search(clean)
        if not singular and not plural:
            return ReferenceResolution(clean, clean)

        context = self._get(platform, owner_id)
        names = [item.resolved_name for item in context.entities] if context else []
        has_authoritative_entity_state = bool(
            context and context.referenced_entities is not None
        )
        if not names and not has_authoritative_entity_state:
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
                source=(
                    "dialogue_state" if context and context.entities else "history"
                ),
                confidence=0.96 if context and context.entities else 0.9,
            )
            for name in selected
        )
        return ReferenceResolution(clean, resolved, entities, True)

    @staticmethod
    def _expected_answer(question: str) -> str:
        clean = question.strip().casefold()
        if re.match(
            r"^(?:do|does|did|is|are|was|were|can|could|would|will|should)\b",
            clean,
        ):
            return "yes_no"
        if re.search(r"\b(?:which|choose|option)\b", clean):
            return "choice"
        if re.search(r"\b(?:when|date|time)\b", clean):
            return "time_or_date"
        return "free_text"

    @staticmethod
    def _goal_payload(state: TurnState) -> dict[str, Any]:
        return {
            "id": state.goal.id,
            "intent": state.goal.intent,
            "summary": state.goal.summary,
            "risk": state.goal.risk,
        }

    def _observed_state(
        self,
        state: TurnState,
        result: Mapping[str, Any],
        previous: DialogueState | None,
        *,
        text: str = "",
        transition: DialogueTransition | None = None,
    ) -> DialogueState:
        current = (previous or DialogueState()).pruned()
        now = time.time()
        owner = state.owner_scope_hash
        source_turn = state.id
        transition = transition or classify_transition(text, current)
        updated = replace(current, updated_at=now)

        if current.last_proactive_message:
            updated.proactive_answered = _slot(
                True,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=86400,
                source="user_response",
                now=now,
            )

        if transition.kind is TransitionClass.REJECTION:
            rejected = list(_slot_value(current.topic_rejections, ()))
            topic = _topic_from_text(text)
            if topic and topic not in rejected:
                rejected.append(topic)
            updated.topic_rejections = _slot(
                tuple(rejected[-12:]),
                source_turn=source_turn,
                owner_scope=owner,
                ttl=7200,
                source="explicit_rejection",
                now=now,
            )

        corrected = (
            _corrected_device(text)
            if transition.kind is TransitionClass.CORRECTION
            else None
        )
        if corrected and current.entities:
            retained = tuple(
                item
                for item in current.entities
                if item.resolved_name.casefold() != corrected.casefold()
            )
            # Keep an authoritative empty set as a short-lived tombstone.  A
            # later pronoun must not resurrect the corrected device from raw
            # transcript history merely because no live entity remains.
            updated.referenced_entities = _slot(
                retained,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=1800,
                source="explicit_correction",
                now=now,
            )

        result_entities = _result_entities(result)
        observed_entities = result_entities or state.entities
        if observed_entities and transition.kind is not TransitionClass.CORRECTION:
            updated.referenced_entities = _slot(
                tuple(observed_entities),
                source_turn=source_turn,
                owner_scope=owner,
                ttl=1800,
                confidence=min(item.confidence for item in observed_entities),
                source="verified_tool" if result_entities else "explicit_turn",
                now=now,
            )

        goal = self._goal_payload(state)
        execution_status = str(result.get("execution_status") or "").casefold()
        failed = execution_status in {"failed", "partial", "cancelled"} or str(
            result.get("model_used") or ""
        ).endswith("_error")
        pending = execution_status in {
            "pending",
            "running",
            "deferred",
            "waiting_approval",
            "clarification",
        }
        response_text = str(result.get("text") or "").strip()
        question = response_text if response_text.endswith("?") else ""

        if transition.kind is TransitionClass.CANCELLATION:
            updated.active_goal = None
            updated.pending_task = None
            updated.pending_approval = None
        elif failed:
            updated.last_failed_goal = _slot(
                goal,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=3600,
                source="execution_result",
                now=now,
            )
            updated.active_goal = None
        elif pending or question:
            updated.active_goal = _slot(
                goal,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=7200,
                source="turn_goal",
                now=now,
            )
        else:
            updated.last_completed_goal = _slot(
                goal,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=3600,
                source="turn_result",
                now=now,
            )
            updated.active_goal = None

        topic = None
        if observed_entities:
            topic = ", ".join(item.resolved_name for item in observed_entities)
        elif transition.kind in {
            TransitionClass.NEW_GOAL,
            TransitionClass.INTERRUPTION,
        }:
            topic = _topic_from_text(text)
        if topic:
            updated.active_topic = _slot(
                topic,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=3600,
                confidence=0.95 if observed_entities else 0.8,
                now=now,
            )

        if question:
            updated.unresolved_assistant_question = _slot(
                question[:500],
                source_turn=source_turn,
                owner_scope=owner,
                ttl=900,
                source="assistant_response",
                now=now,
            )
            updated.expected_answer_type = _slot(
                self._expected_answer(question),
                source_turn=source_turn,
                owner_scope=owner,
                ttl=900,
                source="assistant_response",
                now=now,
            )
        elif transition.kind in {
            TransitionClass.DIRECT_ANSWER,
            TransitionClass.NEW_GOAL,
            TransitionClass.INTERRUPTION,
            TransitionClass.CANCELLATION,
        }:
            updated.unresolved_assistant_question = None
            updated.expected_answer_type = None

        approval_required = bool(result.get("approval_required")) or (
            execution_status == "waiting_approval"
        )
        if approval_required:
            updated.pending_approval = _slot(
                goal,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=86400,
                source="authorization",
                now=now,
            )
        elif transition.kind in {TransitionClass.APPROVAL, TransitionClass.DENIAL}:
            updated.pending_approval = None

        if pending:
            updated.pending_task = _slot(
                goal,
                source_turn=source_turn,
                owner_scope=owner,
                ttl=86400,
                source="execution_result",
                now=now,
            )
        elif (
            transition.kind
            in {
                TransitionClass.CANCELLATION,
                TransitionClass.APPROVAL,
                TransitionClass.DENIAL,
            }
            or not failed
        ):
            updated.pending_task = None

        verification = str(result.get("verification_status") or "")
        if verification in {"verified", "already_satisfied"}:
            updated.last_verified_tool_result = _slot(
                {
                    "goal_id": state.goal.id,
                    "status": verification,
                    "capabilities": [
                        item.capability
                        for item in state.goal.subgoals
                        if item.capability
                    ],
                    "entity_count": len(result_entities),
                },
                source_turn=source_turn,
                owner_scope=owner,
                ttl=3600,
                source="verified_tool_result",
                now=now,
            )
        return updated

    def observe(
        self,
        state: TurnState,
        result: Mapping[str, Any],
        *,
        text: str = "",
        transition: DialogueTransition | None = None,
    ) -> None:
        self.observe_for_owner(
            state.owner_scope_hash,
            state,
            result,
            text=text,
            transition=transition,
        )

    def observe_for_owner(
        self,
        owner_id: str,
        state: TurnState,
        result: Mapping[str, Any],
        *,
        text: str = "",
        transition: DialogueTransition | None = None,
    ) -> None:
        key = self._key(state.platform, str(owner_id))
        hashed_key = self._key(state.platform, state.owner_scope_hash)
        with self._lock:
            previous = self._items.get(key) or self._items.get(hashed_key)
            observed = self._observed_state(
                state,
                result,
                previous,
                text=text,
                transition=transition,
            )
            self._items[hashed_key] = observed
            self._items[key] = observed
            self._items.move_to_end(hashed_key)
            self._items.move_to_end(key)
            while len(self._items) > self.max_sessions:
                self._items.popitem(last=False)

    def record_proactive(
        self,
        *,
        platform: str,
        owner_id: str,
        message_id: str,
        topic: str | None = None,
    ) -> bool:
        """Record one proactive message and reject attempts to stack another."""

        current = self.snapshot(platform=platform, owner_id=owner_id)
        if current.last_proactive_message and not bool(
            _slot_value(current.proactive_answered, False)
        ):
            return False
        now = time.time()
        owner = _owner_scope(owner_id)
        turn = f"proactive:{message_id}"
        current.last_proactive_message = _slot(
            {"message_id": message_id, "topic": topic},
            source_turn=turn,
            owner_scope=owner,
            ttl=86400,
            source="proactive_delivery",
            now=now,
        )
        current.proactive_answered = _slot(
            False,
            source_turn=turn,
            owner_scope=owner,
            ttl=86400,
            source="proactive_delivery",
            now=now,
        )
        current.updated_at = now
        key = self._key(platform, owner_id)
        with self._lock:
            self._items[key] = current
            self._items.move_to_end(key)
        return True

    def export(self, *, platform: str, owner_id: str) -> dict[str, Any]:
        return self.snapshot(platform=platform, owner_id=owner_id).as_dict()

    def clear(self, *, platform: str, owner_id: str) -> None:
        with self._lock:
            self._items.pop(self._key(platform, owner_id), None)
            self._items.pop(self._key(platform, _owner_scope(owner_id)), None)
