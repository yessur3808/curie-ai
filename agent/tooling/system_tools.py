"""Read-only host inspection tools."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections.abc import Mapping
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult
from agent.tooling.errors import ToolExecutionError


def _ram_snapshot() -> tuple[str, dict[str, Any]]:
    import psutil

    vm = psutil.virtual_memory()
    rows = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            rows.append((proc.info["memory_info"].rss, proc.info["pid"], proc.info["name"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    rows.sort(reverse=True)
    top = [{"name": name, "pid": pid, "rss_mib": round(rss / 1024**2, 1)} for rss, pid, name in rows[:8]]
    lines = "\n".join(f"- {row['name']} (PID {row['pid']}): {row['rss_mib']:.1f} MiB" for row in top)
    text = f"RAM: {vm.percent:.1f}% used ({vm.used / 1024**3:.1f}/{vm.total / 1024**3:.1f} GiB)\n\nTop processes:\n{lines}"
    return text, {"percent": vm.percent, "used": vm.used, "total": vm.total, "processes": top}


class RamUsageTool:
    name = "ram_usage"
    read_only = True

    async def execute(self, params: Mapping[str, Any], context: ToolContext) -> ToolResult:
        text, data = await asyncio.to_thread(_ram_snapshot)
        return ToolResult(text=text, data=data, source="psutil")


class HardwareTool:
    name = "hardware"
    read_only = True

    async def execute(self, params: Mapping[str, Any], context: ToolContext) -> ToolResult:
        from llm.accelerators import hardware_status

        data = await asyncio.to_thread(hardware_status)
        return ToolResult(text="```json\n" + json.dumps(data, indent=2, default=str) + "\n```", data=data)


class NetworkSpeedTool:
    """Measure this host's connection without invoking a shell or command sandbox."""

    name = "network_speed"
    read_only = True
    _base_url = "https://speed.cloudflare.com"

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        import httpx

        timeout = httpx.Timeout(20.0, connect=8.0)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                latencies = []
                for _ in range(3):
                    started = time.perf_counter()
                    response = await client.get(
                        f"{self._base_url}/__down", params={"bytes": 0}
                    )
                    response.raise_for_status()
                    latencies.append((time.perf_counter() - started) * 1000)

                started = time.perf_counter()
                downloaded = 0
                async with client.stream(
                    "GET", f"{self._base_url}/__down", params={"bytes": 5_000_000}
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        downloaded += len(chunk)
                download_seconds = max(time.perf_counter() - started, 0.001)

                upload_payload = b"0" * 1_000_000
                started = time.perf_counter()
                response = await client.post(
                    f"{self._base_url}/__up", content=upload_payload
                )
                response.raise_for_status()
                upload_seconds = max(time.perf_counter() - started, 0.001)
        except (httpx.HTTPError, OSError) as exc:
            raise ToolExecutionError(
                f"Network measurement failed: {exc}",
                user_message=(
                    "I could not reach the network measurement service, so no valid "
                    "speed result was produced. The test has stopped. Please try again shortly."
                ),
                retryable=True,
            ) from exc

        latency_ms = statistics.median(latencies)
        download_mbps = downloaded * 8 / download_seconds / 1_000_000
        upload_mbps = len(upload_payload) * 8 / upload_seconds / 1_000_000
        data = {
            "latency_ms": round(latency_ms, 1),
            "download_mbps": round(download_mbps, 1),
            "upload_mbps": round(upload_mbps, 1),
            "samples": len(latencies),
        }
        return ToolResult(
            text=(
                "Network test complete.\n\n"
                f"- Download: {data['download_mbps']:.1f} Mbps\n"
                f"- Upload: {data['upload_mbps']:.1f} Mbps\n"
                f"- Latency (HTTP): {data['latency_ms']:.1f} ms\n\n"
                "These are point-in-time measurements from Curie's host."
            ),
            data=data,
            source="Cloudflare speed test",
        )
