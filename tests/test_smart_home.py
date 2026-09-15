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


def test_dreamview_product_name_resolves_ai_sync_box_when_unique():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake", "sync", "AI Sync Box strip", "light", True, "on", True, True
            ),
            DeviceSnapshot(
                "fake", "floor", "Floor Lamp 2", "light", True, "off", False, True
            ),
        ]
    )

    text, data = asyncio.run(
        SmartHomeHub([provider]).control("owner", "DreamView", "off")
    )

    assert text == "Done. AI Sync Box strip is now off."
    assert data["receipt"]["verified_state"] == "off"
    assert provider.controls == [("owner", "sync", "off")]


def test_batch_control_resolves_every_target_before_running_once():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake", "floor", "Floor Lamp 2", "light", True, "off", False, True
            ),
            DeviceSnapshot(
                "fake", "sync", "AI Sync Box strip", "light", True, "off", False, True
            ),
        ]
    )

    text, data = asyncio.run(
        SmartHomeHub([provider]).control_many("owner", ["floor lamp", "tv light"], "on")
    )

    assert text == "Done. Floor Lamp 2 and AI Sync Box strip are now on."
    assert set(provider.controls) == {
        ("owner", "floor", "on"),
        ("owner", "sync", "on"),
    }
    assert [item["target"] for item in data["correlations"]] == [
        "floor lamp",
        "tv light",
    ]


def test_batch_control_does_not_partially_run_when_one_target_is_unknown():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake", "floor", "Floor Lamp 2", "light", True, "off", False, True
            )
        ]
    )

    with pytest.raises(LookupError, match="mystery light"):
        asyncio.run(
            SmartHomeHub([provider]).control_many(
                "owner", ["floor lamp", "mystery light"], "on"
            )
        )
    assert provider.controls == []


def test_close_device_wording_resolves_when_the_match_is_unique():
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake", "dream", "DreamView T1", "light", True, "off", False, True
            )
        ]
    )

    text, data = asyncio.run(
        SmartHomeHub([provider]).control("owner", "dremview", "on")
    )

    assert text == "Done. DreamView T1 is now on."
    assert data["receipt"]["device_id"] == "dream"


def test_explicit_device_alias_is_persisted_and_owner_scoped(tmp_path, monkeypatch):
    from memory import local_store

    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    provider = FakeProvider(
        [
            DeviceSnapshot(
                "fake", "sync", "AI Sync Box strip", "light", True, "off", False, True
            ),
            DeviceSnapshot(
                "fake", "dream", "DreamView T1", "light", True, "off", False, True
            ),
        ]
    )
    hub = SmartHomeHub([provider])

    text, _ = asyncio.run(
        hub.learn_alias("owner-a", "AI Sync Box Strip", "cinema glow")
    )
    assert text == "Got it — 'cinema glow' means AI Sync Box strip from now on."

    asyncio.run(hub.control("owner-a", "cinema glow", "on"))
    assert provider.controls == [("owner-a", "sync", "on")]
    with pytest.raises(LookupError, match="cinema glow"):
        asyncio.run(hub.control("owner-b", "cinema glow", "off"))


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Can turn on the stand light which is called the dreamview",
            {"target": "dreamview", "state": "on"},
        ),
        (
            "Can you turn on the tv light too",
            {"target": "tv light", "state": "on"},
        ),
    ],
)
def test_natural_single_device_commands_from_recent_chat(text, expected):
    request = classify_request(text)
    assert request.action == "home_control"
    assert request.params["target"] == expected["target"]
    assert request.params["state"] == expected["state"]


def test_multi_device_command_and_plural_reference_keep_the_entity_set():
    first = classify_request("Turn on the floor lamp and tv light")
    assert first.action == "home_control"
    assert first.params["targets"] == ["floor lamp", "tv light"]

    follow_up = classify_request(
        "Please turn them on",
        history=[
            {"role": "user", "content": "Turn on the floor lamp and tv light"},
            {"role": "assistant", "content": "Which two devices?"},
        ],
    )
    assert follow_up.action == "home_control"
    assert follow_up.params["targets"] == ["floor lamp", "tv light"]
    assert follow_up.params["state"] == "on"


def test_try_again_repeats_only_a_recent_failed_home_command():
    failed = classify_request(
        "Try again",
        history=[
            {"role": "user", "content": "Turn on the DreamView"},
            {
                "role": "assistant",
                "content": "Not yet. The command didn't take effect.",
            },
        ],
    )
    assert failed.action == "home_control"
    assert failed.params["target"] == "DreamView"
    assert (
        classify_request(
            "Try again",
            history=[{"role": "assistant", "content": "Done. DreamView T1 is on."}],
        )
        is None
    )


def test_explicit_alias_teaching_routes_to_persistent_home_alias_tool():
    request = classify_request(
        "AI Sync Box Strip is the tv light, please correlate it when mentioned in the future"
    )
    assert request.action == "home_alias"
    assert request.params == {"device": "AI Sync Box Strip", "alias": "tv light"}


def test_home_tools_are_registered_with_expected_policy():
    registry = get_runtime_registry()
    assert registry.get("home_status").risk == "read_only"
    control = registry.get("home_control")
    assert control.risk == "mutating"
    assert control.approval_policy == "never"
    assert "home_control" in control.required_permissions
    alias = registry.get("home_alias")
    assert alias.risk == "mutating"
    assert alias.approval_policy == "never"
    assert "home_control" in alias.required_permissions


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
