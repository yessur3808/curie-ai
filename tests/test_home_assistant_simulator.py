from evaluation.home_assistant_simulator import (
    HomeAssistantSimulator,
    SimulatedEntity,
)


def test_dreamview_alias_resolves_controls_and_verifies():
    simulator = HomeAssistantSimulator()

    result = simulator.control("dreamview", "off")

    assert result.verification_status == "verified"
    assert result.resolved_entity == "light.ai_sync_box_strip"
    assert simulator.entities["light.ai_sync_box_strip"].state == "off"
    assert simulator.calls == [
        {
            "service": "home_assistant.turn_off",
            "entity_id": "light.ai_sync_box_strip",
        }
    ]


def test_already_off_is_verified_without_a_redundant_service_call():
    simulator = HomeAssistantSimulator(
        [SimulatedEntity("light.dreamview", "DreamView", "off")]
    )

    result = simulator.control("dream view", "off")

    assert result.verification_status == "already_satisfied"
    assert result.text == "The DreamView is already off, monsieur."
    assert simulator.calls == []


def test_accepted_but_unchanged_state_is_not_reported_as_success():
    simulator = HomeAssistantSimulator(
        [
            SimulatedEntity(
                "light.dreamview",
                "DreamView",
                "on",
                transition="no_change",
            )
        ]
    )

    result = simulator.control("DreamView", "off", verify_ticks=2)

    assert result.verification_status == "unverified"
    assert "couldn't verify" in result.text
    assert simulator.entities["light.dreamview"].state == "on"


def test_lagged_transition_is_rechecked_before_success():
    simulator = HomeAssistantSimulator(
        [SimulatedEntity("light.dreamview", "DreamView", "on", transition="lagged")]
    )

    result = simulator.control("DreamView", "off", verify_ticks=1)
    assert result.verification_status == "verified"


def test_offline_entity_blocks_without_sending_service_call():
    simulator = HomeAssistantSimulator(
        [SimulatedEntity("light.dreamview", "DreamView", "on", available=False)]
    )

    result = simulator.control("DreamView", "off")
    assert result.verification_status == "failed"
    assert "unavailable" in result.text
    assert simulator.calls == []
