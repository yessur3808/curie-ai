"""Read-only host inspection tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult


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
