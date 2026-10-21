"""Outcome-based, explainable learning for Curie's contribution selection."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Mapping


def _owner_hash(owner_id: str) -> str:
    return hashlib.sha256(str(owner_id or "anonymous").encode()).hexdigest()[:20]


_WEIGHTS = {
    "preview_created": 0.1,
    "preview_approved": 1.5,
    "preview_rejected": -2.0,
    "publish_failed": -0.8,
    "pull_request_created": 2.5,
    "pull_request_merged": 5.0,
    "pull_request_closed": -2.5,
}


class ContributionLearningStore:
    """Small local preference model based only on explicit outcomes."""

    def __init__(self, root: Path | None = None):
        self.root = root or Path.home() / ".curie" / "contribution-learning"
        self._lock = threading.Lock()

    def _path(self, owner_id: str) -> Path:
        return self.root / f"{_owner_hash(owner_id)}.json"

    def _load(self, owner_id: str) -> dict[str, Any]:
        try:
            value = json.loads(self._path(owner_id).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self, owner_id: str, payload: Mapping[str, Any]) -> None:
        path = self._path(owner_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(path)

    def record(
        self,
        owner_id: str,
        event: str,
        *,
        repository: str = "",
        topic: str = "",
        preview_id: str = "",
        url: str = "",
    ) -> None:
        if event not in _WEIGHTS:
            raise ValueError(f"Unsupported contribution outcome: {event}")
        with self._lock:
            data = self._load(owner_id)
            events = list(data.get("events") or [])[-499:]
            events.append(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "event": event,
                    "repository": str(repository)[:200],
                    "topic": str(topic)[:200],
                    "preview_id": str(preview_id)[:32],
                    "url": str(url)[:500],
                }
            )
            self._save(owner_id, {"schema_version": 1, "events": events})

    def score(self, owner_id: str, repository: str, topic: str = "") -> float:
        data = self._load(owner_id)
        total = 0.0
        for item in data.get("events", []):
            weight = _WEIGHTS.get(str(item.get("event") or ""), 0.0)
            if str(item.get("repository") or "").casefold() == repository.casefold():
                total += weight
            if topic and str(item.get("topic") or "").casefold() == topic.casefold():
                total += weight * 0.35
        return round(total, 3)

    def snapshot(self, owner_id: str) -> dict[str, Any]:
        data = self._load(owner_id)
        events = list(data.get("events") or [])
        counts = {
            name: sum(item.get("event") == name for item in events) for name in _WEIGHTS
        }
        return {"events": len(events), "outcomes": counts}


contribution_learning = ContributionLearningStore()


__all__ = ["ContributionLearningStore", "contribution_learning"]
