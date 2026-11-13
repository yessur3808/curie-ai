"""Read-only access to aggregate project analytics cached by Curie Observatory."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.tooling.contracts import ToolContext, ToolResult
from services.observatory_analytics import read_project_analytics


def _count(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unavailable"
    return f"{value:,.0f}"


def _metric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _trend(current: Any, previous: Any) -> str:
    current_value = _metric(current)
    previous_value = _metric(previous)
    if current_value is None or previous_value is None:
        return ""
    if current_value == previous_value:
        return " · no change"
    if previous_value == 0:
        return " · new activity" if current_value > 0 else ""
    change = abs((current_value - previous_value) / previous_value * 100)
    direction = "↑" if current_value > previous_value else "↓"
    return f" · {direction} {change:.0f}% vs previous period"


def _date_label(value: Any) -> str:
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value or "Date unavailable")
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def _period_label(date_range: Mapping[str, Any], *, previous: bool = False) -> str:
    prefix = "previous" if previous else ""
    start = date_range.get(prefix + ("Start" if prefix else "start"))
    end = date_range.get(prefix + ("End" if prefix else "end"))
    if not start or not end:
        return "Period unavailable"
    return f"{_date_label(start)}–{_date_label(end)}"


def _updated_label(value: Any, timezone_name: Any) -> str:
    if not value:
        return "Update time unavailable"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        zone = ZoneInfo(str(timezone_name or "UTC"))
        local = parsed.astimezone(zone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return str(value)
    return f"{local.day} {local.strftime('%b %Y, %H:%M %Z')}"


class ObservatoryProjectAnalyticsTool:
    name = "observatory_project_analytics"
    read_only = True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        query = str(params.get("project") or params.get("query") or "").strip()
        days = int(params.get("days") or 30)
        try:
            report = read_project_analytics(query, days=days)
        except (OSError, ValueError) as exc:
            return ToolResult(
                "**Observatory analytics unavailable**\n\n"
                f"The analytics snapshot could not be read: {exc}",
                {"status": "unavailable"},
                "Curie Observatory local analytics cache",
            )

        status = report["status"]
        if status == "project_required":
            projects = ", ".join(report.get("projects") or ())
            text = "**Select an Observatory project**\n\n" + report["message"]
            if projects:
                text += f"\n\n**Available projects:** {projects}"
            return ToolResult(text, report, "Curie Observatory project configuration")
        if status in {"setup", "not_cached"}:
            return ToolResult(
                "**Observatory analytics unavailable**\n\n" + report["message"],
                report,
                "Curie Observatory local analytics cache",
            )

        project = report["project"]["name"]
        date_range = report.get("range") or {}
        period = _period_label(date_range)
        totals = report.get("totals") or {}
        previous = report.get("previous") or {}
        source = report.get("source") or "GA4"
        lines = [
            f"**{project} — Analytics overview**",
            "",
            "**Reporting period**",
            f"{report['days']} complete days · {period}",
            f"**Source:** {source} · **Status:** {status.title()}",
            "",
            "**Key metrics**",
            f"- **Page views:** {_count(totals.get('screenPageViews'))}"
            + _trend(totals.get("screenPageViews"), previous.get("screenPageViews")),
            f"- **Active users:** {_count(totals.get('activeUsers'))}"
            + _trend(totals.get("activeUsers"), previous.get("activeUsers")),
            f"- **Sessions:** {_count(totals.get('sessions'))}"
            + _trend(totals.get("sessions"), previous.get("sessions")),
            f"- **New users:** {_count(totals.get('newUsers'))}"
            + _trend(totals.get("newUsers"), previous.get("newUsers")),
        ]
        if previous:
            lines.extend(
                (
                    "",
                    "**Comparison period**",
                    f"{_period_label(date_range, previous=True)} · "
                    f"{_count(previous.get('screenPageViews'))} views · "
                    f"{_count(previous.get('activeUsers'))} active users · "
                    f"{_count(previous.get('sessions'))} sessions",
                )
            )
        if report.get("message"):
            lines.extend(("", "**Provider note**", str(report["message"])))
        views = _metric(totals.get("screenPageViews"))
        users = _metric(totals.get("activeUsers"))
        sessions = _metric(totals.get("sessions"))
        if views == 0 and users == 0 and sessions == 0:
            lines.extend(
                (
                    "",
                    "**Assessment**",
                    "GA4 recorded no activity during this reporting period. If visits were expected, verify the production analytics tag and consent settings.",
                )
            )
        if report.get("updatedAt"):
            lines.extend(
                (
                    "",
                    f"_Updated {_updated_label(report['updatedAt'], report.get('timeZone'))} · Read-only Observatory snapshot_",
                )
            )
        return ToolResult(
            "\n".join(lines),
            report,
            "Curie Observatory local analytics cache / GA4",
        )


__all__ = ["ObservatoryProjectAnalyticsTool"]
