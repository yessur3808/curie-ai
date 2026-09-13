import asyncio

import pytest

from agent.intent_router import classify_request
from agent.tooling import ToolContext, get_runtime_registry
from agent.tooling.smart_home_tools import HomeStatusTool
from services.smart_home.cloud import GoveeProvider, SmartThingsProvider
from services.smart_home.hub import SmartHomeHub
from services.smart_home.local import _lan_host
from services.smart_home.models import ControlReceipt, DeviceSnapshot


class FakeProvider:
    name = "fake"

    def __init__(self, devices):
        self.devices = devices
        self.controls = []

    def configured(self, owner_id):
        return True, None

    async def list_devices(self, owner_id):
        return list(self.devices)

    async def set_power(self, owner_id, device_id, state):
        self.controls.append((owner_id, device_id, state))
        old = next(item for item in self.devices if item.device_id == device_id)
        new = DeviceSnapshot(
            old.provider,
            old.device_id,
            old.name,
            old.device_type,
            True,
            state,
            state == "on",
            old.controllable,
            old.metrics,
            old.attributes,
        )
        self.devices = [
            new if item.device_id == device_id else item for item in self.devices
        ]
        return ControlReceipt(self.name, device_id, old.name, state, state, new)


def test_home_summary_distinguishes_power_connectivity_and_metrics():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake",
                "lamp",
                "Bedroom Lamp",
                "light",
                True,
                "on",
                True,
                True,
                {"brightness_percent": 50},
            ),
            DeviceSnapshot(
                "fake", "plug", "Desk Plug", "plug", True, "off", False, True
            ),
            DeviceSnapshot(
                "fake",
                "sensor",
                "Door Sensor",
                "sensor",
                False,
                "unknown",
                None,
                False,
                {"battery": 12},
            ),
        ]
    )
    text, data = asyncio.run(SmartHomeHub([provider]).status("owner"))

    assert data["counts"] == {
        "total": 3,
        "on": 1,
        "off": 1,
        "offline": 1,
        "unknown_power": 1,
        "running": 1,
    }
    assert "1 on, 1 off, 1 offline" in text
    assert "low battery" in text
    assert "Door Sensor [fake]: offline" in text


def test_exact_control_runs_without_bulk_or_ambiguous_target():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake", "desk", "Desk Plug", online=True, power="on", controllable=True
            ),
            DeviceSnapshot(
                "fake",
                "bed",
                "Bedroom Plug",
                online=True,
                power="off",
                controllable=True,
            ),
        ]
    )
    hub = SmartHomeHub([provider])

    text, data = asyncio.run(hub.control("owner", "Desk Plug", "off"))
    assert text == "Done. Desk Plug is now off."
    assert data["receipt"]["verified_state"] == "off"
    assert provider.controls == [("owner", "desk", "off")]

    with pytest.raises(ValueError, match="ambiguous"):
        asyncio.run(hub.control("owner", "plug", "on"))
    with pytest.raises(ValueError, match="Bulk"):
        asyncio.run(hub.control("owner", "everything", "off"))


def test_tv_light_alias_prefers_the_only_online_tv_light():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake", "sync", "AI Sync Box strip", "light", True, "on", True, True
            ),
            DeviceSnapshot(
                "fake", "dream", "DreamView T1", "light", False, "on", True, True
            ),
        ]
    )

    text, _ = asyncio.run(SmartHomeHub([provider]).control("owner", "tv light", "off"))

    assert text == "Done. AI Sync Box strip is now off."
    assert provider.controls == [("owner", "sync", "off")]


def test_device_already_in_requested_state_gets_natural_noop_reply():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake",
                "dream",
                "DreamView T1",
                "light",
                True,
                "off",
                False,
                True,
            )
        ]
    )

    text, data = asyncio.run(
        SmartHomeHub([provider]).control("owner", "dreamview", "off")
    )

    assert text == "The DreamView T1 is already off, monsieur."
    assert data["already_in_state"] is True
    assert data["receipt"]["verified_state"] == "off"
    assert provider.controls == []


def test_offline_device_reports_the_blocker_without_claiming_success():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake",
                "dream",
                "DreamView T1",
                "light",
                False,
                "off",
                False,
                True,
            )
        ]
    )

    with pytest.raises(ConnectionError, match="can't reach DreamView T1"):
        asyncio.run(SmartHomeHub([provider]).control("owner", "dreamview", "off"))
    assert provider.controls == []


@pytest.mark.parametrize(
    ("text", "action", "target", "state"),
    [
        ("What's running at home?", "home_status", "", None),
        ("Is the bedroom lamp on?", "home_status", "bedroom lamp", None),
        ("Turn off the desk plug", "home_control", "desk plug", "off"),
        (
            "Switch the living-room Nanoleaf on",
            "home_control",
            "living-room Nanoleaf",
            "on",
        ),
        ("/home off Desk Plug", "home_control", "Desk Plug", "off"),
    ],
)
def test_smart_home_intents_are_deterministic(text, action, target, state):
    request = classify_request(text)
    assert request.action == action
    assert request.params["target"] == target
    assert request.needs_approval is False
    if state:
        assert request.params["state"] == state


def test_pronoun_control_asks_for_a_device_name():
    request = classify_request("Turn it off")
    assert request.action == "clarify"
    assert "which home device" in request.params["message"].lower()


def test_pronoun_control_uses_recent_explicit_home_target():
    request = classify_request(
        "Turn it off",
        history=[
            {"role": "user", "content": "Turn off the tv light"},
            {
                "role": "assistant",
                "content": "I couldn't find a device matching that name.",
            },
        ],
    )

    assert request.action == "home_control"
    assert request.params == {"target": "tv light", "state": "off", "provider": ""}


def test_home_tools_are_registered_with_expected_policy():
    registry = get_runtime_registry()
    assert registry.get("home_status").risk == "read_only"
    control = registry.get("home_control")
    assert control.risk == "mutating"
    assert control.approval_policy == "never"
    assert "home_control" in control.required_permissions


def test_home_status_is_owner_only_when_master_is_configured(monkeypatch):
    monkeypatch.setenv("MASTER_USER_ID", "owner")
    with pytest.raises(PermissionError, match="configured owner"):
        asyncio.run(HomeStatusTool().execute({}, ToolContext("someone-else")))


def test_public_network_targets_are_rejected():
    with pytest.raises(ValueError, match="private LAN"):
        _lan_host("8.8.8.8")
    assert _lan_host("192.168.1.10") == "192.168.1.10"
    assert _lan_host("homeassistant.local") == "homeassistant.local"


def test_smartthings_normalizes_switch_health_and_telemetry():
    snapshot = SmartThingsProvider._snapshot(
        {
            "deviceId": "abc",
            "label": "Hall Light",
            "health": {"state": "ONLINE"},
            "status": {
                "components": {
                    "main": {
                        "switch": {"switch": {"value": "on"}},
                        "battery": {"battery": {"value": 88}},
                    }
                }
            },
        }
    )
    assert (snapshot.power, snapshot.online, snapshot.controllable) == (
        "on",
        True,
        True,
    )
    assert snapshot.metrics["battery"] == 88


def test_govee_normalizes_capability_state():
    snapshot = GoveeProvider._snapshot(
        {"device": "dev", "deviceName": "Strip", "sku": "H123"},
        {
            "payload": {
                "capabilities": [
                    {"instance": "online", "state": {"value": True}},
                    {"instance": "powerSwitch", "state": {"value": 1}},
                ]
            }
        },
    )
    assert (snapshot.power, snapshot.online, snapshot.controllable) == (
        "on",
        True,
        True,
    )
