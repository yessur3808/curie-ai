"""Requested morning weather and noteworthy portfolio analytics briefs."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import ssl

import httpx
from zoneinfo import ZoneInfo

from memory import UserManager
from memory.local_store import reserve_mutation_attempt, finish_mutation_attempt
from services.proactive_policy import delivery_allowed, delivery_updates


async def refresh_portfolio_cache(projects):
    """Ask the existing private dashboard to refresh its own GA4 cache."""
    from services.observatory_analytics import observatory_data_dir

    root = observatory_data_dir()
    key_path = root / "access-key"
    ca_path = root.parent / "certs" / "ca.pem"
    if not key_path.is_file() or not ca_path.is_file():
        return
    context = ssl.create_default_context(cafile=str(ca_path))
    async with httpx.AsyncClient(verify=context, timeout=50, trust_env=False) as client:
        for project in projects:
            try:
                await client.get(
                    "https://100.104.252.183:8443/api/analytics",
                    params={"project": project, "days": 7},
                    headers={"Authorization": "Bearer " + key_path.read_text().strip()},
                )
            except httpx.HTTPError:
                # Existing snapshots remain readable and will be flagged stale.
                continue


def weather_brief(report):
    days = report["days"]
    if len(days) != 4:
        raise ValueError("Four forecast days are required")
    today = days[0]
    lines = [
        f"Bonjour! A little meteorology before your day begins in {report['city']}."
    ]
    for index, day in enumerate(days):
        label = (
            "Today"
            if index == 0
            else datetime.fromisoformat(day["date"]).strftime("%a")
        )
        rain = day.get("precipitation_probability")
        rain_text = (
            f"{rain}% rain chance" if rain is not None else "rain chance unavailable"
        )
        lines.append(
            f"{label}: {day['low']:g}–{day['high']:g}°C, {day['description'].lower()}; {rain_text}."
        )
    delta = days[-1]["high"] - today["high"]
    comparison = (
        f"By {datetime.fromisoformat(days[-1]['date']).strftime('%A')}, highs look {abs(delta):g}°C {'warmer' if delta > 0 else 'cooler'}."
        if abs(delta) >= 1
        else "Highs look fairly steady over the next three days."
    )
    advice = (
        " I’d take an umbrella today."
        if (today.get("precipitation_probability") or 0) >= 40
        else ""
    )
    lines.append(comparison + advice)
    lines.append("Open-Meteo forecast; later days may change.")
    return "\n\n".join([lines[0], "\n".join(lines[1:5]), " ".join(lines[5:])])


def analytics_brief(reports, now, *, initial=False):
    lines, signature = [], []
    for report in reports:
        name = report.get("project", {}).get("name", "Project")
        status = report.get("status")
        updated = report.get("updatedAt")
        try:
            stamp = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stale = now - stamp > timedelta(hours=48)
        except ValueError:
            stale = True
        totals, previous = report.get("totals", {}), report.get("previous", {})
        if status != "live" or stale:
            issue = (
                status if status in {"setup", "not_cached", "error"} else "stale data"
            )
            lines.append(
                f"{name}: {issue}; I can’t judge current performance from this snapshot."
            )
            signature.append((name, issue))
            continue
        users, old = totals.get("activeUsers"), previous.get("activeUsers")
        views = totals.get("screenPageViews")
        if not isinstance(users, (int, float)) or not isinstance(views, (int, float)):
            continue
        significant = (
            isinstance(old, (int, float))
            and abs(users - old) >= 10
            and (old == 0 or abs(users - old) / old >= 0.2)
        )
        if not significant and not initial:
            continue
        if not isinstance(old, (int, float)):
            lines.append(
                f"{name}: {users:g} active users, {views:g} views over {report['days']} days; comparison unavailable."
            )
            signature.append((name, users, views))
            continue
        change = (
            f"{'up' if users > old else 'down'} {abs(users-old)/old:.0%}"
            if old
            else ("new activity" if users else "no recorded activity")
        )
        span = report.get("range", {})
        lines.append(
            f"{name}: {users:g} active users ({change} vs the previous {report['days']} days), {views:g} views. Period: {span.get('start', '?')}–{span.get('end', '?')}."
        )
        signature.append((name, users, old, views, span))
    if not lines:
        return None
    text = (
        "A few numbers caught my eye in your Observatory, monsieur.\n\n"
        + "\n\n".join(lines)
    )
    text += "\n\nThese are analytics signals, not a diagnosis of what caused them."
    return (
        text,
        hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest(),
    )


class PersonalBriefWorker:
    def __init__(self, send):
        self.send = send

    async def run_once(self):
        from memory.repositories import get_repositories

        owners = {
            str(row["internal_id"])
            for row in get_repositories().profiles.list_with_identities()
        }
        for owner in owners:
            await self.run_owner(owner)

    async def _deliver(self, owner, key, text):
        from memory.repositories import get_repositories

        target = get_repositories().identities.get_external_id(owner, "telegram")
        if not target:
            return False
        if reserve_mutation_attempt(
            owner, key, "personal_brief", hashlib.sha256(text.encode()).hexdigest()
        ):
            return False
        try:
            sent = await asyncio.wait_for(
                self.send("telegram", target, text), timeout=30
            )
        except Exception:
            finish_mutation_attempt(owner, key, "uncertain")
            return False
        finish_mutation_attempt(owner, key, "completed" if sent else "uncertain")
        if sent:
            from memory.session_store import get_session_manager

            get_session_manager().add_message("telegram", owner, "assistant", text)
        return bool(sent)

    async def run_owner(self, owner, now=None):
        now = now or datetime.now(timezone.utc)
        profile = UserManager.get_user_profile(owner) or {}
        local = now.astimezone(ZoneInfo(profile.get("timezone") or "Asia/Hong_Kong"))
        if (
            profile.get("morning_weather_enabled")
            and local.hour == 6
            and local.minute >= 30
        ):
            date = local.date().isoformat()
            if profile.get("morning_weather_attempt_date") != date:
                attempted = profile.get("morning_weather_fetch_at")
                if attempted and now - datetime.fromisoformat(attempted) < timedelta(
                    minutes=5
                ):
                    return
                UserManager.update_user_profile(
                    owner, {"morning_weather_fetch_at": now.isoformat()}
                )
                from utils.weather import get_weather_range

                report = await get_weather_range(
                    profile.get("morning_weather_city") or "Hong Kong",
                    local.date(),
                    local.date() + timedelta(days=3),
                )
                text = weather_brief(report)
                # Explicit requested briefing is independent of unsolicited quiet hours.
                await self._deliver(owner, f"morning-weather:{date}", text)
                UserManager.update_user_profile(
                    owner, {"morning_weather_attempt_date": date}
                )
        if not profile.get("portfolio_briefs_enabled"):
            return
        last = profile.get("portfolio_briefs_checked_at")
        if last and now - datetime.fromisoformat(last) < timedelta(hours=1):
            return
        UserManager.update_user_profile(
            owner, {"portfolio_briefs_checked_at": now.isoformat()}
        )
        from services.observatory_analytics import read_project_analytics

        catalog = await asyncio.to_thread(read_project_analytics, "", days=7)
        reports = []
        names = catalog.get("projects", [])
        if not names and catalog.get("project"):
            reports = [catalog]
        initial = [
            await asyncio.to_thread(read_project_analytics, name, days=7)
            for name in names[:30]
        ]
        if not names:
            initial = reports
        await refresh_portfolio_cache(
            [report["project"]["id"] for report in initial if report.get("project")]
        )
        if not names and catalog.get("project"):
            reports = [
                await asyncio.to_thread(
                    read_project_analytics, catalog["project"]["id"], days=7
                )
            ]
        for name in names[:30]:
            report = await asyncio.to_thread(read_project_analytics, name, days=7)
            if report.get("status") == "not_cached" and 30 in report.get(
                "availableDays", []
            ):
                report = await asyncio.to_thread(read_project_analytics, name, days=30)
            reports.append(report)
        result = analytics_brief(
            reports, now, initial=not profile.get("portfolio_briefs_baseline_sent")
        )
        if result:
            text, fingerprint = result
            allowed, _ = delivery_allowed(profile, "portfolio analytics", now)
            if not allowed:
                return
            if await self._deliver(owner, f"portfolio-brief:{fingerprint}", text):
                UserManager.update_user_profile(
                    owner, {"portfolio_briefs_baseline_sent": True}
                )
                UserManager.update_user_profile(
                    owner,
                    delivery_updates(
                        profile,
                        "portfolio analytics",
                        "A significant analytics change or tracking gap was detected.",
                        now,
                    ),
                )
