"""Per-feature resource budgets and adaptive runtime throttling.

The governor deliberately stores only aggregate counters and hashed owner scopes.
It never records prompts, responses, credentials, or other user content.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping


def _owner_hash(owner_id: str) -> str:
    return hashlib.sha256(str(owner_id or "anonymous").encode()).hexdigest()[:20]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class FeatureBudget:
    tokens_per_day: int
    wall_seconds_per_hour: float
    calls_per_hour: int
    minimum_tokens: int = 96
    background: bool = False


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    feature: str
    mode: str
    max_tokens: int
    model: str
    downgraded: bool
    reason: str


_DEFAULTS: dict[str, FeatureBudget] = {
    "conversation": FeatureBudget(250_000, 1_800, 240, 96),
    "autonomous_learning": FeatureBudget(32_000, 900, 24, 256, True),
    "x_research": FeatureBudget(1_000, 300, 2, 96, True),
    "open_source_planning": FeatureBudget(8_000, 600, 8, 256, True),
    "open_source_coding": FeatureBudget(20_000, 1_200, 8, 512, True),
    "proactive_messaging": FeatureBudget(12_000, 300, 36, 96, True),
    "action_preview": FeatureBudget(8_000, 300, 120, 64),
}


class ResourceGovernor:
    """Rolling budgets plus pressure-aware admission and model selection."""

    def __init__(self, path: Path | None = None):
        instance = os.getenv("CURIE_INSTANCE", "default").strip().casefold()
        self.path = path or Path(
            os.getenv("CURIE_RESOURCE_BUDGET_PATH")
            or Path.home() / ".curie" / f"resource-budgets-{instance}.json"
        )
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._events = [
                item for item in payload.get("events", []) if isinstance(item, dict)
            ][-4000:]
        except (OSError, ValueError, TypeError):
            self._events = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"schema_version": 1, "events": self._events[-4000:]}, indent=2
                ),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(self.path)
        except OSError:
            pass

    @staticmethod
    def _env_int(name: str, fallback: int) -> int:
        try:
            return max(1, int(os.getenv(name, str(fallback))))
        except ValueError:
            return fallback

    def budget_for(self, feature: str) -> FeatureBudget:
        feature = str(feature or "conversation").strip().casefold()
        base = _DEFAULTS.get(feature, _DEFAULTS["conversation"])
        prefix = "CURIE_BUDGET_" + feature.upper().replace("-", "_")
        return FeatureBudget(
            self._env_int(f"{prefix}_TOKENS_DAY", base.tokens_per_day),
            float(
                self._env_int(f"{prefix}_SECONDS_HOUR", int(base.wall_seconds_per_hour))
            ),
            self._env_int(f"{prefix}_CALLS_HOUR", base.calls_per_hour),
            self._env_int(f"{prefix}_MIN_TOKENS", base.minimum_tokens),
            base.background,
        )

    def pressure_mode(
        self, diagnostics: Mapping[str, Any] | None = None
    ) -> tuple[str, str]:
        snapshot = dict(diagnostics or {})
        last = dict(snapshot.get("last") or {})
        p95 = float(snapshot.get("p95_latency_ms") or 0)
        rss = float(last.get("rss_end_mb") or 0)
        warnings = set(last.get("warnings") or ())
        severe_ms = float(os.getenv("CURIE_GOVERNOR_SEVERE_LATENCY_MS", "18000"))
        constrained_ms = float(
            os.getenv("CURIE_GOVERNOR_CONSTRAINED_LATENCY_MS", "7000")
        )
        severe_rss = float(os.getenv("CURIE_GOVERNOR_SEVERE_RSS_MB", "7168"))
        constrained_rss = float(os.getenv("CURIE_GOVERNOR_CONSTRAINED_RSS_MB", "5120"))
        if (
            p95 >= severe_ms
            or rss >= severe_rss
            or {"command_error", "high_memory"} <= warnings
        ):
            return "paused", "runtime pressure is severe"
        if p95 >= constrained_ms or rss >= constrained_rss or "slow_model" in warnings:
            return "constrained", "runtime pressure is elevated"
        return "normal", "runtime pressure is healthy"

    def _usage(self, owner_hash: str, feature: str, now: datetime) -> dict[str, float]:
        hour_ago, day_ago = now - timedelta(hours=1), now - timedelta(days=1)
        tokens = calls = seconds = 0.0
        retained: list[dict[str, Any]] = []
        for item in self._events:
            try:
                stamp = datetime.fromisoformat(str(item["at"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            if stamp >= day_ago:
                retained.append(item)
            if item.get("owner") != owner_hash or item.get("feature") != feature:
                continue
            if stamp >= day_ago:
                tokens += float(item.get("tokens") or 0)
            if stamp >= hour_ago:
                calls += 1
                seconds += float(item.get("wall_seconds") or 0)
        self._events = retained[-4000:]
        return {"tokens_day": tokens, "calls_hour": calls, "seconds_hour": seconds}

    def admit(
        self,
        feature: str,
        owner_id: str,
        requested_tokens: int,
        *,
        preferred_model: str = "",
        fallback_model: str = "",
        diagnostics: Mapping[str, Any] | None = None,
    ) -> BudgetDecision:
        feature = str(feature or "conversation").strip().casefold()
        budget = self.budget_for(feature)
        now = _utcnow()
        with self._lock:
            usage = self._usage(_owner_hash(owner_id), feature, now)
        mode, pressure_reason = self.pressure_mode(diagnostics)
        remaining = max(0, budget.tokens_per_day - int(usage["tokens_day"]))
        if usage["calls_hour"] >= budget.calls_per_hour:
            return BudgetDecision(
                False, feature, "paused", 0, "", False, "hourly call budget reached"
            )
        if usage["seconds_hour"] >= budget.wall_seconds_per_hour:
            return BudgetDecision(
                False, feature, "paused", 0, "", False, "hourly runtime budget reached"
            )
        if remaining < budget.minimum_tokens:
            return BudgetDecision(
                False, feature, "paused", 0, "", False, "daily token budget reached"
            )
        if mode == "paused" and budget.background:
            return BudgetDecision(False, feature, mode, 0, "", False, pressure_reason)
        requested = max(budget.minimum_tokens, int(requested_tokens))
        ceiling = min(requested, remaining)
        downgraded = False
        model = preferred_model
        reason = "within budget"
        preserve_conversation_quality = feature == "conversation" and os.getenv(
            "CURIE_CONVERSATION_ALLOW_MODEL_DOWNGRADE", "false"
        ).lower() not in {"1", "true", "yes"}
        if (
            mode == "constrained" or remaining < requested * 2
        ) and not preserve_conversation_quality:
            ceiling = max(
                budget.minimum_tokens,
                min(ceiling, max(budget.minimum_tokens, requested // 2)),
            )
            if fallback_model:
                model = fallback_model
                downgraded = model != preferred_model
            reason = (
                pressure_reason
                if mode == "constrained"
                else "token budget is nearing its limit"
            )
        return BudgetDecision(True, feature, mode, ceiling, model, downgraded, reason)

    def record(
        self,
        feature: str,
        owner_id: str,
        *,
        tokens: int = 0,
        wall_seconds: float = 0,
        model: str = "",
        outcome: str = "completed",
    ) -> None:
        event = {
            "at": _utcnow().isoformat(),
            "owner": _owner_hash(owner_id),
            "feature": str(feature or "conversation").strip().casefold(),
            "tokens": max(0, int(tokens)),
            "wall_seconds": round(max(0.0, float(wall_seconds)), 3),
            "model": str(model or "")[:160],
            "outcome": str(outcome or "completed")[:40],
        }
        with self._lock:
            self._events.append(event)
            self._usage(event["owner"], event["feature"], _utcnow())
            self._save()

    def snapshot(self, owner_id: str = "") -> dict[str, Any]:
        now = _utcnow()
        owner_hash = _owner_hash(owner_id)
        with self._lock:
            rows = {}
            for feature in sorted(_DEFAULTS):
                budget = self.budget_for(feature)
                usage = self._usage(owner_hash, feature, now)
                rows[feature] = {
                    "budget": asdict(budget),
                    "usage": usage,
                    "remaining_tokens": max(
                        0, budget.tokens_per_day - int(usage["tokens_day"])
                    ),
                }
        try:
            from services.self_diagnostics import diagnostic_monitor

            diagnostics = diagnostic_monitor.snapshot(owner_id=owner_id, limit=50)
        except Exception:
            diagnostics = {}
        mode, reason = self.pressure_mode(diagnostics)
        return {
            "captured_at": now.isoformat(),
            "mode": mode,
            "reason": reason,
            "features": rows,
        }


def render_resource_budgets(owner_id: str) -> str:
    snapshot = resource_governor.snapshot(owner_id)
    lines = [
        "**Curie resource budgets**",
        "",
        f"- **Runtime mode:** {snapshot['mode']} — {snapshot['reason']}",
        "",
        "| Feature | Tokens today | Remaining | Calls this hour | Runtime this hour |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in snapshot["features"].items():
        usage, budget = row["usage"], row["budget"]
        lines.append(
            f"| {name.replace('_', ' ').title()} | {int(usage['tokens_day']):,} | "
            f"{int(row['remaining_tokens']):,} | {int(usage['calls_hour'])}/{budget['calls_per_hour']} | "
            f"{float(usage['seconds_hour']):.1f}/{float(budget['wall_seconds_per_hour']):.0f}s |"
        )
    return "\n".join(lines)


def handle_resource_budget_command(text: str, owner_id: str) -> str | None:
    if str(text or "").strip().casefold() not in {
        "/budgets",
        "/resource-budgets",
        "show resource budgets",
    }:
        return None
    return render_resource_budgets(owner_id)


resource_governor = ResourceGovernor()


__all__ = [
    "BudgetDecision",
    "FeatureBudget",
    "ResourceGovernor",
    "handle_resource_budget_command",
    "render_resource_budgets",
    "resource_governor",
]
