"""Official Hong Kong bus route and arrival data; no API key required."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import re
from zoneinfo import ZoneInfo

import httpx
from utils.ttl_cache import TTLCache

BASES = {
    "CTB": "https://rt.data.gov.hk/v2/transport/citybus",
    "KMB": "https://data.etabus.gov.hk/v1/transport/kmb",
}
_METADATA = TTLCache(
    ttl_seconds=86400,
    max_size=4096,
    name="hk_bus_metadata",
    owner_scope="public",
    invalidation_event="TTL expiry",
    sensitivity="public",
)


def normalize(value):
    return re.sub(r"[^\w]+", " ", str(value).lower()).strip()


class BusClient:
    def __init__(self, client):
        self.client = client
        self.limit = asyncio.Semaphore(8)

    async def get(self, operator, path, *, cache=False):
        url = BASES[operator] + "/" + path
        if cache:
            value = _METADATA.get(url)
            if value is not None:
                return value
        async with self.limit:
            response = await self.client.get(url)
            response.raise_for_status()
            value = response.json()["data"]
        if cache:
            _METADATA.set(url, value)
        return value

    async def stops(self, operator, route, direction, service_type="1"):
        path = (
            f"route-stop/CTB/{route}/{direction}"
            if operator == "CTB"
            else f"route-stop/{route}/{direction}/{service_type}"
        )
        rows = await self.get(operator, path, cache=True)

        async def enrich(row):
            stop = await self.get(operator, "stop/" + row["stop"], cache=True)
            return {**row, "name_en": stop["name_en"]}

        return sorted(
            await asyncio.gather(*(enrich(row) for row in rows)),
            key=lambda row: int(row["seq"]),
        )

    async def lookup(
        self, operator, route, origin, destination="", direction="", service_type="1"
    ):
        operator = operator.upper()
        route = route.upper()
        if operator not in BASES or not re.fullmatch(r"[A-Z0-9]{1,8}", route):
            raise ValueError("Specify Citybus (CTB) or KMB and a valid route number.")
        if direction not in ("", "inbound", "outbound") or not re.fullmatch(
            r"\d{1,2}", service_type
        ):
            raise ValueError("Use inbound/outbound and a numeric service type.")
        if not origin.strip():
            raise ValueError("Please specify the boarding stop.")
        directions = [direction] if direction else ["outbound", "inbound"]
        variants = await asyncio.gather(
            *(self.stops(operator, route, d, service_type) for d in directions)
        )
        matches = []
        for d, rows in zip(directions, variants):
            for row in rows:
                if origin != row["stop"] and normalize(origin) not in normalize(
                    row["name_en"]
                ):
                    continue
                if destination and not any(
                    normalize(destination) in normalize(end["name_en"])
                    and int(end["seq"]) > int(row["seq"])
                    for end in rows
                ):
                    continue
                matches.append((d, row))
        if not matches:
            raise ValueError(
                f"No {operator} {route} journey matches {origin} → {destination or 'the specified direction'}. Check the boarding stop and direction."
            )
        if len(matches) != 1:
            raise ValueError(
                "Please specify a destination or direction; more than one boarding stop matches."
            )
        d, stop = matches[0]
        path = (
            f"eta/CTB/{stop['stop']}/{route}"
            if operator == "CTB"
            else f"eta/{stop['stop']}/{route}/{service_type}"
        )
        rows = await self.get(operator, path)
        now = datetime.now(timezone.utc)
        arrivals, remarks = [], []
        for row in rows:
            # Citybus ETA seq may refer to a short-working variant, not the
            # full route-stop sequence (18X is one such route). The endpoint
            # identifies the boarding stop; repeated-stop routes are ambiguous above.
            if row.get("dir") != d[0].upper():
                continue
            if operator == "KMB" and (
                str(row.get("service_type")) != service_type
                or str(row.get("seq")) != str(stop["seq"])
            ):
                continue
            remark = row.get("rmk_en", "")
            if remark and remark not in remarks:
                remarks.append(remark)
            try:
                updated = datetime.fromisoformat(row["data_timestamp"])
                eta = datetime.fromisoformat(row["eta"])
                if updated.tzinfo is None or eta.tzinfo is None:
                    continue
                if (now - updated).total_seconds() > 180 or eta < now:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            arrivals.append(
                {
                    "eta": eta.isoformat(),
                    "updated_at": updated.isoformat(),
                    "minutes": math.ceil((eta - now).total_seconds() / 60),
                    "time_hk": eta.astimezone(ZoneInfo("Asia/Hong_Kong")).strftime(
                        "%H:%M"
                    ),
                    "destination": row.get("dest_en", ""),
                    "remark": remark,
                }
            )
        arrivals = sorted(
            {a["eta"]: a for a in arrivals}.values(), key=lambda a: a["eta"]
        )[:3]
        return {
            "operator": operator,
            "route": route,
            "stop": stop["stop"],
            "boarding_stop": stop["name_en"],
            "destination": destination,
            "direction": d,
            "arrivals": arrivals,
            "remarks": remarks,
            "checked_at": now.isoformat(),
            "source": BASES[operator] + "/" + path,
        }


async def lookup_bus(**kwargs):
    async with httpx.AsyncClient(timeout=10) as client:
        return await asyncio.wait_for(BusClient(client).lookup(**kwargs), timeout=25)
