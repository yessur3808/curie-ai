from dataclasses import replace
from datetime import datetime, timezone

import pytest

pytest.importorskip("_curie_device_resolver")

from agent.understanding.entities import (
    DeviceResolver,
    ResolutionStatus,
    device_resolver_metrics,
    device_resolver_status,
)
from services.smart_home.aliases import DeviceAlias, normalize_alias
from services.smart_home.models import CanonicalDevice, DeviceSnapshot


def _device(
    name, device_id, *, kind="light", room=None, online=True, controllable=True
):
    snapshot = DeviceSnapshot(
        "fake",
        device_id,
        name,
        kind,
        online,
        "off",
        False,
        controllable,
        {},
        {"room": room} if room else {},
    )
    return CanonicalDevice.from_snapshot(
        snapshot, normalized_name=normalize_alias(name)
    )


@pytest.fixture(autouse=True)
def require_rust(monkeypatch):
    monkeypatch.setenv("CURIE_DEVICE_RESOLVER", "rust")
    device_resolver_metrics(reset=True)


def test_strict_status_reports_native_without_fallback():
    assert device_resolver_status() == {
        "mode": "rust",
        "available": True,
        "active": "rust",
        "version": "rust-device-resolver-v1",
        "fallback": False,
        "import_error": None,
    }


@pytest.mark.parametrize(
    "target",
    ["all lights", "all of the lights", "both lamps", "every bulb"],
)
def test_generic_natural_light_wording_resolves_the_whole_safe_group(target):
    devices = [
        _device("AI Sync Box strip", "sync", room="living room"),
        _device("Floor Lamp 2", "floor", room="living room"),
        _device("Desk Fan", "fan", kind="fan", controllable=True),
    ]
    result = DeviceResolver().resolve(target, devices)
    assert result.status is ResolutionStatus.GROUP
    assert [item.provider_id for item in result.devices] == ["sync", "floor"]


def test_room_online_and_controllable_groups_are_explainable():
    devices = [
        _device("Kitchen Strip", "kitchen", room="kitchen"),
        _device("Bedroom Lamp", "bed", room="bedroom", online=False),
        _device("Hall Switch", "switch", kind="switch"),
    ]
    resolver = DeviceResolver()
    assert [
        item.provider_id
        for item in resolver.resolve("lights in the kitchen", devices).devices
    ] == ["kitchen"]
    assert [
        item.provider_id
        for item in resolver.resolve("all online lights", devices).devices
    ] == ["kitchen"]
    assert (
        resolver.resolve("all controllable devices", devices).status
        is ResolutionStatus.GROUP
    )


def test_alias_tombstone_canonical_id_and_close_wording_follow_strict_order():
    dream = _device("DreamView T1", "dream")
    sync = _device("AI Sync Box strip", "sync")
    alias = DeviceAlias(
        "tv light",
        "tv light",
        "confirmed",
        sync.canonical_id,
        sync.provider,
        sync.provider_id,
        sync.display_name,
        1.0,
        "explicit",
        datetime.now(timezone.utc).isoformat(),
        None,
    )
    resolver = DeviceResolver()
    assert resolver.resolve("tv light", [dream, sync], aliases=[alias]).devices == (
        sync,
    )
    rejected = replace(
        alias,
        alias="DreamView",
        normalized_alias="dreamview",
        status="rejected",
        device_key=None,
    )
    assert (
        resolver.resolve("DreamView", [dream, sync], aliases=[rejected]).status
        is ResolutionStatus.REJECTED_ALIAS
    )
    assert resolver.resolve(
        dream.canonical_id, [dream, sync], aliases=[rejected]
    ).devices == (dream,)
    assert resolver.resolve("dremview", [dream, sync]).devices == (dream,)


def test_unique_display_light_semantics_resolve_without_an_explicit_alias():
    sync = _device("AI Sync Box strip", "sync")
    floor = _device("Floor Lamp 2", "floor")

    result = DeviceResolver().resolve("tv light", [sync, floor])

    assert result.devices == (sync,)
    assert result.reason == "unique_display_light_semantics"
    assert result.confidence == pytest.approx(0.96)


def test_close_candidates_are_ambiguous_and_recent_plural_reference_is_stable():
    one = _device("Floor Lamp One", "one")
    two = _device("Floor Lamp Two", "two")
    resolver = DeviceResolver()
    assert (
        resolver.resolve("floor lamp", [one, two]).status is ResolutionStatus.AMBIGUOUS
    )
    recent = resolver.resolve(
        "both devices",
        [one, two],
        recent_entity_ids=[one.canonical_id, two.canonical_id],
    )
    assert recent.devices == (one, two)
    assert recent.reason == "dialogue_state_reference"


def test_rust_matches_python_rollback_for_existing_resolution_contract(monkeypatch):
    devices = [
        _device("Floor Lamp 2", "floor", room="living room"),
        _device("Kitchen Lamp", "kitchen", room="kitchen"),
        _device("Hall Switch", "switch", kind="switch", room="hall"),
    ]
    scenarios = [
        (devices[0].canonical_id, {}),
        ("floor light", {}),
        ("flor lamp 2", {}),
        ("kitchen lights", {}),
        ("all of the lights", {}),
        ("all online lights", {}),
        ("all switches", {}),
        ("tv light", {}),
        ("missing mystery", {"consequential": False}),
    ]
    resolver = DeviceResolver()
    for target, kwargs in scenarios:
        monkeypatch.setenv("CURIE_DEVICE_RESOLVER", "python")
        python = resolver.resolve(target, devices, **kwargs)
        monkeypatch.setenv("CURIE_DEVICE_RESOLVER", "rust")
        rust = resolver.resolve(target, devices, **kwargs)
        assert rust.status is python.status
        assert rust.reason == python.reason
        assert [item.canonical_id for item in rust.devices] == [
            item.canonical_id for item in python.devices
        ]
        assert rust.candidates == python.candidates
        assert rust.confidence == pytest.approx(python.confidence)


def test_metrics_record_native_resolution_without_content():
    DeviceResolver().resolve("dreamview", [_device("DreamView T1", "dream")])
    assert device_resolver_metrics() == {
        "resolutions": 1,
        "native_resolutions": 1,
        "python_resolutions": 0,
        "native_failures": 0,
        "native_index_builds": 1,
        "native_index_hits": 0,
    }
