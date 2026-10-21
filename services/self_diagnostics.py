"""Privacy-safe, per-command resource and latency diagnostics for Curie."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import statistics
import threading
import time
from typing import Any, Mapping


def _rss_bytes() -> int:
    """Return current RSS where possible, falling back to process peak RSS."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS/BSD report bytes.
    return int(usage if os.uname().sysname == "Darwin" else usage * 1024)


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime + usage.ru_stime)


def _owner_hash(owner_id: str) -> str:
    return hashlib.sha256(str(owner_id or "anonymous").encode()).hexdigest()[:16]


def _token_estimate(value: str) -> int:
    return max(1, int(len(str(value or "").split()) * 1.33)) if value else 0


@dataclass(frozen=True, slots=True)
class DiagnosticTicket:
    started_wall: float
    started_cpu: float
    started_rss: int
    owner_scope_hash: str
    connector: str


class DiagnosticMonitor:
    """Bounded diagnostic recorder that never stores message contents."""

    def __init__(self, path: Path | None = None, max_records: int = 250):
        instance = os.getenv("CURIE_INSTANCE", "default").strip().casefold()
        filename = (
            "command-diagnostics.json"
            if instance == "default"
            else f"instance-{instance}-command-diagnostics.json"
        )
        self.path = path or Path.home() / ".curie" / filename
        self._records: deque[dict[str, Any]] = deque(maxlen=max(10, int(max_records)))
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for item in payload.get("records", [])[-self._records.maxlen :]:
                if isinstance(item, dict):
                    self._records.append(item)
        except (OSError, ValueError, TypeError):
            pass

    def start(
        self, *, owner_id: str = "", connector: str = "unknown"
    ) -> DiagnosticTicket:
        return DiagnosticTicket(
            started_wall=time.perf_counter(),
            started_cpu=_cpu_seconds(),
            started_rss=_rss_bytes(),
            owner_scope_hash=_owner_hash(owner_id),
            connector=str(connector or "unknown")[:40],
        )

    def finish(
        self,
        ticket: DiagnosticTicket,
        result: Mapping[str, Any] | None,
        *,
        error: BaseException | None = None,
    ) -> dict[str, Any]:
        result = dict(result or {})
        elapsed_ms = round((time.perf_counter() - ticket.started_wall) * 1000, 2)
        cpu_seconds = max(0.0, _cpu_seconds() - ticket.started_cpu)
        rss_end = _rss_bytes()
        timings = {
            str(key): round(float(value), 2)
            for key, value in dict(result.get("timings_ms") or {}).items()
            if isinstance(value, (int, float))
        }
        telemetry = dict(result.get("telemetry") or {})
        response = str(result.get("text") or "")
        prompt_tokens = int(telemetry.get("prompt_tokens_estimate") or 0)
        output_tokens = int(
            telemetry.get("output_tokens")
            or telemetry.get("output_tokens_estimate")
            or _token_estimate(response)
        )
        model_ms = float(
            telemetry.get("total_ms")
            or timings.get("model_response")
            or timings.get("execute")
            or 0
        )
        warnings: list[str] = []
        latency_warn = float(os.getenv("CURIE_DIAGNOSTIC_LATENCY_WARN_MS", "5000"))
        rss_warn = int(os.getenv("CURIE_DIAGNOSTIC_RSS_WARN_MB", "4096")) * 1024 * 1024
        if elapsed_ms >= latency_warn:
            warnings.append("high_latency")
        if model_ms and model_ms >= latency_warn * 0.75:
            warnings.append("slow_model")
        if rss_end >= rss_warn:
            warnings.append("high_memory")
        if error is not None:
            warnings.append("command_error")
        record = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "owner_scope_hash": ticket.owner_scope_hash,
            "connector": ticket.connector,
            "model": str(result.get("model_used") or "unknown")[:160],
            "status": (
                "error" if error else str(result.get("input_status") or "completed")
            ),
            "latency_ms": elapsed_ms,
            "stage_latency_ms": timings,
            "cpu_ms": round(cpu_seconds * 1000, 2),
            "cpu_percent_of_wall": round(
                min(999.0, cpu_seconds / max(elapsed_ms / 1000, 0.001) * 100), 1
            ),
            "rss_start_mb": round(ticket.started_rss / 1024**2, 1),
            "rss_end_mb": round(rss_end / 1024**2, 1),
            "rss_delta_mb": round((rss_end - ticket.started_rss) / 1024**2, 1),
            "prompt_tokens_estimate": prompt_tokens,
            "output_tokens_estimate": output_tokens,
            "queue_wait_ms": round(float(telemetry.get("queue_wait_ms") or 0), 2),
            "first_token_ms": round(float(telemetry.get("first_token_ms") or 0), 2),
            "warnings": sorted(set(warnings)),
            "error_type": type(error).__name__ if error else None,
        }
        with self._lock:
            self._records.append(record)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(
                        {"schema_version": 1, "records": list(self._records)}, indent=2
                    ),
                    encoding="utf-8",
                )
                temporary.chmod(0o600)
                temporary.replace(self.path)
            except OSError:
                pass
        return record

    def snapshot(self, *, owner_id: str = "", limit: int = 25) -> dict[str, Any]:
        owner_scope_hash = _owner_hash(owner_id)
        with self._lock:
            records = [
                dict(item)
                for item in self._records
                if not owner_id or item.get("owner_scope_hash") == owner_scope_hash
            ][-max(1, min(int(limit), 100)) :]
        latencies = [float(item.get("latency_ms") or 0) for item in records]
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0
        return {
            "count": len(records),
            "last": records[-1] if records else None,
            "average_latency_ms": (
                round(statistics.fmean(latencies), 2) if latencies else 0
            ),
            "p95_latency_ms": round(p95, 2),
            "warning_counts": {
                name: sum(name in item.get("warnings", []) for item in records)
                for name in (
                    "high_latency",
                    "slow_model",
                    "high_memory",
                    "command_error",
                )
            },
            "records": records,
        }

    def trends(
        self, *, owner_id: str = "", hours: int = 24, bucket_minutes: int = 30
    ) -> dict[str, Any]:
        """Return bounded dashboard-ready latency, token, CPU, and RSS trends."""
        owner_scope_hash = _owner_hash(owner_id)
        now = datetime.now(timezone.utc)
        horizon = max(1, min(int(hours), 168)) * 3600
        bucket_seconds = max(300, min(int(bucket_minutes), 360)) * 60
        buckets: dict[int, list[dict[str, Any]]] = {}
        with self._lock:
            source = list(self._records)
        for item in source:
            if owner_id and item.get("owner_scope_hash") != owner_scope_hash:
                continue
            try:
                stamp = datetime.fromisoformat(
                    str(item.get("recorded_at") or "").replace("Z", "+00:00")
                )
            except ValueError:
                continue
            age = (now - stamp).total_seconds()
            if age < 0 or age > horizon:
                continue
            key = int(stamp.timestamp()) // bucket_seconds * bucket_seconds
            buckets.setdefault(key, []).append(item)
        points = []
        for key, rows in sorted(buckets.items()):
            latencies = sorted(float(row.get("latency_ms") or 0) for row in rows)
            p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
            points.append(
                {
                    "at": datetime.fromtimestamp(key, timezone.utc).isoformat(),
                    "commands": len(rows),
                    "latency_avg_ms": round(statistics.fmean(latencies), 2),
                    "latency_p95_ms": round(p95, 2),
                    "cpu_ms": round(
                        sum(float(row.get("cpu_ms") or 0) for row in rows), 2
                    ),
                    "rss_peak_mb": round(
                        max(float(row.get("rss_end_mb") or 0) for row in rows), 2
                    ),
                    "tokens": sum(
                        int(row.get("prompt_tokens_estimate") or 0)
                        + int(row.get("output_tokens_estimate") or 0)
                        for row in rows
                    ),
                    "warnings": sum(bool(row.get("warnings")) for row in rows),
                }
            )
        snapshot = self.snapshot(owner_id=owner_id, limit=100)
        try:
            from services.resource_governor import resource_governor

            mode, reason = resource_governor.pressure_mode(snapshot)
        except Exception:
            mode, reason = "unknown", "resource governor unavailable"
        return {
            "captured_at": now.isoformat(),
            "hours": max(1, min(int(hours), 168)),
            "bucket_minutes": bucket_seconds // 60,
            "mode": mode,
            "throttle_reason": reason,
            "summary": {
                key: value for key, value in snapshot.items() if key != "records"
            },
            "points": points,
        }


def network_snapshot(timeout: float = 1.5) -> dict[str, Any]:
    """Perform bounded DNS/TCP checks without sending application data."""
    target = os.getenv("CURIE_DIAGNOSTIC_NETWORK_TARGET", "api.github.com:443")
    host, _, port_text = target.partition(":")
    try:
        port = int(port_text or "443")
    except ValueError:
        port = 443
    started = time.perf_counter()
    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        dns_ms = round((time.perf_counter() - started) * 1000, 2)
        connect_started = time.perf_counter()
        with socket.create_connection(resolved[0][4], timeout=max(0.2, timeout)):
            pass
        return {
            "ready": True,
            "target": host,
            "dns_ms": dns_ms,
            "connect_ms": round((time.perf_counter() - connect_started) * 1000, 2),
        }
    except (OSError, socket.gaierror) as exc:
        return {
            "ready": False,
            "target": host,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": type(exc).__name__,
        }


def render_diagnostics(owner_id: str = "", *, verbose: bool = False) -> str:
    snapshot = diagnostic_monitor.snapshot(owner_id=owner_id, limit=25)
    last = snapshot.get("last") or {}
    network = network_snapshot() if verbose else None
    try:
        from services.resource_governor import resource_governor

        governor_mode, governor_reason = resource_governor.pressure_mode(snapshot)
    except Exception:
        governor_mode, governor_reason = "unknown", "resource governor unavailable"
    lines = ["**Curie self-diagnostics**", ""]
    if not last:
        lines.append("- No completed commands have been measured in this process yet.")
    else:
        lines.extend(
            [
                f"- **Last command:** {last['latency_ms']:.0f} ms total; {last['cpu_ms']:.0f} ms CPU",
                f"- **Memory:** {last['rss_end_mb']:.1f} MB RSS ({last['rss_delta_mb']:+.1f} MB during command)",
                f"- **Model:** {last['model']}",
                f"- **Tokens:** about {last['prompt_tokens_estimate']} in / {last['output_tokens_estimate']} out",
                f"- **Recent latency:** {snapshot['average_latency_ms']:.0f} ms average; {snapshot['p95_latency_ms']:.0f} ms p95 across {snapshot['count']} command(s)",
                f"- **Warnings:** {', '.join(last['warnings']) if last['warnings'] else 'None'}",
                f"- **Automatic throttling:** {governor_mode} — {governor_reason}",
            ]
        )
    if verbose and last:
        lines.extend(["", "**Last-command detail**"])
        for name, value in sorted(dict(last.get("stage_latency_ms") or {}).items()):
            lines.append(f"- **{name.replace('_', ' ').title()}:** {value:.0f} ms")
        lines.extend(
            [
                f"- **CPU share:** {last['cpu_percent_of_wall']:.1f}% of one core during the command",
                f"- **Queue wait:** {last['queue_wait_ms']:.0f} ms",
                f"- **First token:** {last['first_token_ms']:.0f} ms",
            ]
        )
        if network:
            if network["ready"]:
                lines.append(
                    f"- **Network:** Ready; DNS {network['dns_ms']:.0f} ms, TCP {network['connect_ms']:.0f} ms to {network['target']}"
                )
            else:
                lines.append(
                    f"- **Network:** Degraded; {network['error']} reaching {network['target']}"
                )
        records = snapshot.get("records", [])[-5:]
        if records:
            lines.extend(
                [
                    "",
                    "**Recent commands**",
                    "",
                    "| Time | Latency | CPU | RSS | Tokens |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for item in reversed(records):
                stamp = str(item.get("recorded_at") or "")[11:19]
                tokens = int(item.get("prompt_tokens_estimate") or 0) + int(
                    item.get("output_tokens_estimate") or 0
                )
                lines.append(
                    f"| {stamp} | {float(item.get('latency_ms') or 0):.0f} ms | {float(item.get('cpu_ms') or 0):.0f} ms | {float(item.get('rss_end_mb') or 0):.1f} MB | ~{tokens} |"
                )
    if not verbose:
        lines.extend(
            [
                "",
                "Use `/diagnostics verbose` for stage timings, network health, and recent-command detail.",
            ]
        )
    return "\n".join(lines)


def handle_diagnostics_command(text: str, owner_id: str = "") -> str | None:
    cleaned = str(text or "").strip().casefold()
    if not (
        cleaned in {"/diagnostics", "/diagnostics summary", "/diagnostics verbose"}
        or cleaned.startswith("show curie diagnostics")
        or cleaned.startswith("show your diagnostics")
    ):
        return None
    return render_diagnostics(owner_id, verbose="verbose" in cleaned)


diagnostic_monitor = DiagnosticMonitor()
