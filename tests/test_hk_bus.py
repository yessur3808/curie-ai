import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agent.action_router import classify_request
from agent.tooling.bus_tool import BusArrivalsTool
from agent.tooling.contracts import ToolContext
from agent.tooling.registry import get_runtime_registry
from utils.hk_bus import BusClient, _METADATA

pytestmark = pytest.mark.security


@pytest.mark.parametrize(
    "query",
    [
        "Check my morning bus commute",
        "Check my afternoon commute",
        "18X from Ka On Street to Healthy Village",
        "18x or 33x from model housing estate to connaught garden",
        "Track KMB 1A from Star Ferry to Sau Mau Ping",
    ],
)
def test_routes_to_bus_tool(query):
    assert classify_request(query).action == "bus_arrivals"
    assert get_runtime_registry().get("bus_arrivals").resource_policy.network


@pytest.mark.parametrize(
    "operator,route,direction",
    [("CTB", "18X", "O"), ("CTB", "33X", "I"), ("KMB", "1A", "O")],
)
def test_provider_filters_direction_sequence_service_and_stale_data(
    operator, route, direction
):
    _METADATA.clear()
    now = datetime.now(timezone.utc)

    def eta(**overrides):
        return dict(
            route=route,
            dir=direction,
            seq=1,
            service_type=1,
            eta=(now + timedelta(minutes=5)).isoformat(),
            data_timestamp=now.isoformat(),
            **overrides,
        )

    valid = eta()
    records = [
        valid,
        {**valid, "dir": "I" if direction == "O" else "O"},
        {**valid, "eta": None},
        {**valid, "eta": (now - timedelta(minutes=5)).isoformat()},
        {
            **valid,
            "eta": (now + timedelta(minutes=9)).isoformat(),
            "data_timestamp": (now - timedelta(minutes=10)).isoformat(),
        },
    ]
    if operator == "CTB":
        records[0]["seq"] = 11  # Short-working Citybus variant shares this stop.
    if operator == "KMB":
        records.append({**valid, "seq": 3})
        records.append(
            {
                **valid,
                "service_type": 2,
                "eta": (now + timedelta(minutes=8)).isoformat(),
            }
        )

    def handler(request):
        path = request.url.path
        if "/route-stop/" in path:
            data = [{"stop": "a", "seq": 1}, {"stop": "b", "seq": 2}]
        elif "/stop/" in path:
            data = {
                "name_en": (
                    "Model Housing Estate"
                    if path.endswith("/a")
                    else "Connaught Garden"
                )
            }
        else:
            data = records
        return httpx.Response(200, json={"data": data})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await BusClient(client).lookup(
                operator,
                route,
                "Model Housing Estate",
                "Connaught Garden",
                "outbound" if direction == "O" else "inbound",
            )

    result = asyncio.run(run())
    assert len(result["arrivals"]) == 1
    assert result["arrivals"][0]["minutes"] == 5


def test_return_commute_checks_both_routes_and_preserves_partial_failure(monkeypatch):
    calls = []

    async def lookup(**kwargs):
        calls.append(kwargs)
        if kwargs["route"] == "33X":
            raise httpx.ConnectError("offline")
        return dict(
            operator="CTB",
            route="18X",
            boarding_stop=kwargs["origin"],
            destination=kwargs["destination"],
            direction="outbound",
            arrivals=[],
            remarks=[],
        )

    monkeypatch.setattr("agent.tooling.bus_tool.lookup_bus", lookup)
    result = asyncio.run(
        BusArrivalsTool().execute(
            {"query": "Check my evening bus commute"}, ToolContext("test")
        )
    )
    assert [c["route"] for c in calls] == ["18X", "33X"]
    assert all(c["origin"] == "Model Housing Estate" for c in calls)
    assert "No current arrival estimate" in result.text
    assert "33X: Live bus data is unavailable" in result.text


def test_morning_uses_confirmed_macao_ferry_stop(monkeypatch):
    calls = []

    async def lookup(**kwargs):
        calls.append(kwargs)
        raise ValueError("test")

    monkeypatch.setattr("agent.tooling.bus_tool.lookup_bus", lookup)
    asyncio.run(
        BusArrivalsTool().execute(
            {"query": "Check my morning commute"}, ToolContext("test")
        )
    )
    assert calls[0]["operator"] == "CTB"
    assert calls[0]["route"] == "18X"
    assert calls[0]["origin"] == "Central (Macao Ferry)"
    assert calls[0]["destination"] == "Healthy Village"


def test_explicit_origin_and_k11_alias(monkeypatch):
    calls = []

    async def lookup(**kwargs):
        calls.append(kwargs)
        raise ValueError("test")

    monkeypatch.setattr("agent.tooling.bus_tool.lookup_bus", lookup)
    for query in [
        "18X from Ka On Street to Healthy Village",
        "33X from K11 Atelier to Connaught Garden",
    ]:
        asyncio.run(BusArrivalsTool().execute({"query": query}, ToolContext("test")))
    assert calls[0]["origin"] == "Ka On Street"
    assert calls[0]["destination"] == "Healthy Village"
    assert calls[1]["origin"] == "Model Housing Estate"


def test_reply_exposes_check_time_and_provider_time(monkeypatch):
    async def lookup(**kwargs):
        return dict(
            operator="CTB",
            route="18X",
            boarding_stop="Central (Macao Ferry)",
            destination="Healthy Village",
            direction="inbound",
            remarks=[],
            checked_at="2026-10-29T02:06:00+00:00",
            arrivals=[
                dict(
                    minutes=13, time_hk="10:19", updated_at="2026-10-29T10:05:53+08:00"
                )
            ],
        )

    monkeypatch.setattr("agent.tooling.bus_tool.lookup_bus", lookup)
    result = asyncio.run(
        BusArrivalsTool().execute({"query": "Update me on 18x"}, ToolContext("test"))
    )
    assert "13 min (10:19)" in result.text
    assert "Checked at 10:06:00 HKT; provider updated 10:05:53 HKT" in result.text
    assert "do not refresh" in result.text
