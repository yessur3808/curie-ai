"""Bus arrival tool and Hong Kong commute shortcuts."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import httpx
from agent.tooling.contracts import ToolContext, ToolResult
from utils.hk_bus import lookup_bus


class BusArrivalsTool:
    name = "bus_arrivals"
    read_only = True

    async def execute(self, params, context: ToolContext):
        query = str(params.get("query", ""))
        lowered = query.lower()
        routes = re.findall(r"\b\d{1,3}[a-z]?\b", query, re.I)
        route = str(params.get("route") or (routes[0] if routes else "")).upper()
        operator = str(
            params.get("operator") or ("KMB" if "kmb" in lowered else "CTB")
        ).upper()
        operator = {"CITYBUS": "CTB"}.get(operator, operator)
        origin = str(params.get("origin", ""))
        destination = str(params.get("destination", ""))
        from_match = re.search(r"\bfrom\s+(.+?)(?:\s+to\s+(.+?))?[?.!]*$", query, re.I)
        if from_match:
            origin = origin or from_match.group(1).strip()
            destination = destination or (from_match.group(2) or "").strip()
        returning = bool(
            re.search(r"\b(?:home|evening|afternoon|connaught garden)\b", lowered)
        )
        commute = "commute" in lowered or route in {"18X", "33X"}
        if operator == "CTB" and commute and returning and not origin:
            origin, destination = "Model Housing Estate", "Connaught Garden"
        if "k11" in origin.lower():
            origin = "Model Housing Estate"
        if operator == "CTB" and commute and not returning:
            route = route or "18X"
            destination = destination or "Healthy Village"
            if route == "18X":
                origin = origin or "Central (Macao Ferry)"
        if not origin:
            return ToolResult(
                "Which stop are you boarding at? For morning 18X to Healthy Village, "
                "options include Ka On Street or Sun Yat Sen Memorial Park. "
                "Connaught Garden is served on the return journey only."
            )
        selected = (
            [route] if route else (["18X", "33X"] if returning and commute else [])
        )
        if returning and commute and "18x" in lowered and "33x" in lowered:
            selected = ["18X", "33X"]
        if not selected:
            return ToolResult(
                "Which KMB or Citybus route should I check? Include your boarding stop and destination."
            )

        async def check(number):
            try:
                return await lookup_bus(
                    operator=operator,
                    route=number,
                    origin=origin,
                    destination=destination,
                    direction=str(params.get("direction", "")),
                    service_type=str(params.get("service_type", "1")),
                )
            except ValueError as exc:
                return {"route": number, "error": str(exc)}
            except (httpx.HTTPError, asyncio.TimeoutError, KeyError, TypeError):
                return {
                    "route": number,
                    "error": "Live bus data is unavailable. Please try again shortly.",
                }

        results = await asyncio.gather(*(check(number) for number in selected))
        lines = []
        for result in results:
            if "error" in result:
                lines.append(f"{result['route']}: {result['error']}")
                continue
            times = ", ".join(
                f"{a['minutes']} min ({a['time_hk']})" for a in result["arrivals"]
            )
            lines.append(
                f"{result['operator']} {result['route']} — {result['boarding_stop']} → "
                f"{result['destination'] or result['direction']}: "
                f"{times or 'No current arrival estimate available; this does not confirm the service has ended.'}"
            )
            checked = result.get("checked_at")
            if checked:
                checked_hk = (
                    datetime.fromisoformat(checked)
                    .astimezone(ZoneInfo("Asia/Hong_Kong"))
                    .strftime("%H:%M:%S")
                )
                updated = [
                    a["updated_at"] for a in result["arrivals"] if a.get("updated_at")
                ]
                freshness = f"Checked at {checked_hk} HKT"
                if updated:
                    oldest = min(datetime.fromisoformat(value) for value in updated)
                    freshness += f"; provider updated {oldest.astimezone(ZoneInfo('Asia/Hong_Kong')).strftime('%H:%M:%S')} HKT"
                lines.append(freshness + ".")
            if result["remarks"]:
                lines.append("; ".join(result["remarks"]))
        lines.append(
            "Times are Hong Kong time. Source: official Citybus / KMB live arrival feeds. "
            "Countdowns are as of the check time and do not refresh in this message. "
            "Ask again for an updated estimate."
        )
        return ToolResult(
            "\n".join(lines),
            data={"journeys": results},
            source="DATA.GOV.HK / Citybus / KMB",
        )
