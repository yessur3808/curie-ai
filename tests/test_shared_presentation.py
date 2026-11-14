import pytest
from agent.presentation import message, voice_spec
from agent.persona_contract import active_persona, build_persona_contract
from agent.orchestration.response_policy import ResponsePolicy


class Identity:
    def apply_response_style(self, response, *args, **kwargs):
        return response


def test_recovery_and_diagnostics_commands_reach_workflow():
    from connectors.telegram import WORKFLOW_COMMANDS

    assert {"selfheal", "recovery", "diagnostics"} <= set(WORKFLOW_COMMANDS)


def test_compact_contract_uses_single_shared_spec():
    persona = active_persona()
    assert build_persona_contract(persona, medium="voice", compact=True) == voice_spec(
        persona
    )
    assert len(voice_spec(persona)) < 240
    for medium in ("chat", "tool result", "voice", "research", "scheduled brief"):
        assert voice_spec(persona) in build_persona_contract(persona, medium=medium)


def test_structured_compact_contract_keeps_output_rules():
    assert "machine-readable" in build_persona_contract(
        medium="json", compact=True, structured_output=True
    )


def test_templates_no_inference_and_preserve_values(monkeypatch):
    import llm.manager

    def forbidden(*a, **k):
        raise AssertionError("No model call allowed")

    monkeypatch.setattr(llm.manager, "ask_llm", forbidden)
    assert "Bonjour" in message("weather", city="Hong Kong")
    assert "12–15°C https://example.org/a?q=2" in message(
        "weather", city="12–15°C https://example.org/a?q=2"
    )
    assert "Bonjour" not in message("weather", city="Hong Kong", english_only=True)
    for kind in (
        "unavailable",
        "attachment",
        "voice_unheard",
        "analytics",
        "research",
        "sources_saved",
        "resumed",
    ):
        assert message(kind)
    with pytest.raises(ValueError):
        message("arbitrary-shell-command")


def test_other_personas_do_not_inherit_french():
    other = {"name": "Andreja", "response_style": {"tone": "sardonic"}}
    assert "French" not in voice_spec(other)
    assert "Bonjour" not in message("weather", persona=other, city="Hong Kong")


def test_natural_operational_french_is_preserved_not_canned_tags():
    policy = ResponsePolicy(active_persona(), Identity())
    assert (
        policy.finalize("Voilà. The lamp is off.", "Turn off the lamp")
        == "Voilà. The lamp is off."
    )
    assert "voilà" not in policy.finalize("Done, voilà.", "Turn off the lamp")
    assert "Voilà" not in policy.finalize(
        "Voilà. The lamp is off.", "English only: turn off the lamp"
    )


@pytest.mark.parametrize(
    "text,user_text",
    [
        ('{"x":"Curie: voilà","n":12.5}', "Return JSON"),
        ("\x60\x60\x60python\nprint('Curie: voilà')\n\x60\x60\x60", "Return code"),
        ("Voilà. 12–15°C; https://example.org/?q=2", "Repeat verbatim"),
    ],
)
def test_payloads_not_styled(text, user_text):
    assert (
        ResponsePolicy(active_persona(), Identity()).finalize(text, user_text) == text
    )
