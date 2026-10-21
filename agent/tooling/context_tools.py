"""Live situational-awareness tools for flights, places, and Curie's dashboard."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult


class FlightTrackerTool:
    name, read_only = "flight_track", True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from services.flight_tracking import track_flight

        result = await track_flight(
            str(params["flight_number"]), date=str(params.get("date") or "") or None
        )
        rows = result["status"]
        lines = [f"**Flight {result['flight_number']}**"]
        if rows:
            for row in rows[:3]:
                departure, arrival = row["departure"], row["arrival"]
                lines.extend(
                    (
                        f"- **Status:** {row.get('status') or 'Unknown'}",
                        f"- **Route:** {departure.get('iata') or departure.get('airport') or '?'} → "
                        f"{arrival.get('iata') or arrival.get('airport') or '?'}",
                        f"- **Departure:** {departure.get('estimated') or departure.get('scheduled') or 'Not supplied'}"
                        + (
                            f" · Terminal {departure['terminal']}"
                            if departure.get("terminal")
                            else ""
                        )
                        + (
                            f" · Gate {departure['gate']}"
                            if departure.get("gate")
                            else ""
                        ),
                        f"- **Arrival:** {arrival.get('estimated') or arrival.get('scheduled') or 'Not supplied'}"
                        + (
                            f" · Terminal {arrival['terminal']}"
                            if arrival.get("terminal")
                            else ""
                        )
                        + (f" · Gate {arrival['gate']}" if arrival.get("gate") else ""),
                    )
                )
        else:
            lines.append(
                "Live schedule data is not configured or no provider match was returned."
            )
        live = result.get("live_position")
        if live:
            lines.extend(
                (
                    "",
                    "**Live position**",
                    f"- {live.get('latitude')}, {live.get('longitude')} · "
                    f"{live.get('barometric_altitude_m')} m · {live.get('speed_mps')} m/s",
                    f"- [Open live map]({live.get('map_url')})",
                )
            )
        links = result["links"]
        lines.extend(
            (
                "",
                f"[Google status]({links['google']}) · "
                f"[FlightAware]({links['flightaware']}) · "
                f"[Flightradar24]({links['flightradar24']})",
                "",
                f"*Source checked now. {result['limitations']}*",
            )
        )
        return ToolResult("\n".join(lines), result, "aviationstack / OpenSky")


class PlacesSearchTool:
    name, read_only = "places_search", True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from services.places import search_places

        result = await search_places(
            str(params["query"]),
            limit=int(params.get("limit", 5)),
            open_now=bool(params.get("open_now", False)),
            latitude=params.get("latitude"),
            longitude=params.get("longitude"),
            radius_meters=float(params.get("radius_meters", 5000)),
        )
        lines = [f"**Places for {result['query']}**", ""]
        if not result["places"]:
            lines.append(
                "Detailed place results need `GOOGLE_MAPS_API_KEY`, but the map search is ready."
            )
        for place in result["places"]:
            detail = []
            if place.get("rating") is not None:
                detail.append(
                    f"{place['rating']}★ ({place.get('rating_count') or 0} ratings)"
                )
            if place.get("open_now") is not None:
                detail.append("Open now" if place["open_now"] else "Closed now")
            lines.append(f"- **[{place['name']}]({place['maps_url']})**")
            if place.get("address"):
                lines.append(f"  {place['address']}")
            if detail:
                lines.append("  " + " · ".join(detail))
            lines.append(f"  [Directions]({place['directions_url']})")
        lines.extend(("", f"[Open this search in Google Maps]({result['search_url']})"))
        return ToolResult("\n".join(lines), result, result["provider"])


class DashboardSnapshotTool:
    name, read_only = "dashboard_snapshot", True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from services.dashboard_context import dashboard_snapshot

        result = dashboard_snapshot()
        lines = [f"**Curie dashboard: {str(result['overall_status']).title()}**", ""]
        for row in result["instances"]:
            lines.append(
                f"- **{row['instance']}:** {row['health']} · {row['persona']} · "
                f"{row['activity']} · CPU {row['cpu']} · RAM {row['memory']}"
            )
            lines.append(
                f"  Model: {row['model']} · Connectors: {row['connectors']} · "
                f"Errors: {row['errors']}"
            )
        degraded = [name for name, ready in result["readiness"].items() if not ready]
        if degraded:
            lines.extend(("", "**Needs attention:** " + ", ".join(degraded)))
        else:
            lines.extend(("", "All reported readiness checks are healthy."))
        lines.append(f"*Captured {result['captured_at']}* ")
        return ToolResult("\n".join(lines), result, "Curie dashboard runtime")


__all__ = ["DashboardSnapshotTool", "FlightTrackerTool", "PlacesSearchTool"]
