from agent.persona_contract import (
    apply_persona_contract,
    build_persona_contract,
)
from utils.persona import load_persona


def test_contract_preserves_full_curie_identity_across_media():
    persona = load_persona("curie.json")

    for medium in (
        "Telegram message",
        "public X post",
        "voice reply",
        "technical explanation",
        "long-form writing",
    ):
        contract = build_persona_contract(persona, medium=medium)
        assert "[ACTIVE PERSONA: Curie]" in contract
        assert persona["system_prompt"] in contract
        assert medium in contract
        assert "never fall back to a generic assistant" in contract
        assert "light French identity" in contract


def test_structured_contract_keeps_schema_strict():
    prompt = apply_persona_contract(
        "Return JSON with a suggestion key.",
        medium="proactive prediction",
        structured_output=True,
    )

    assert "machine-readable structure with no extra prose" in prompt
    assert prompt.endswith("Return JSON with a suggestion key.")


def test_compact_contract_preserves_identity_without_consuming_context():
    contract = build_persona_contract(
        load_persona("curie.json"),
        medium="local-model travel plan",
        compact=True,
    )

    assert len(contract) < 450
    assert "first principles" in contract
    assert "admit uncertainty" in contract
    assert "Curie:" in contract
    assert "curious" in contract
    assert "French" in contract
    assert "dry wit" in contract


def test_wisdom_guidance_preserves_curie_identity():
    persona = load_persona("curie.json")
    for structured in (False, True):
        contract = build_persona_contract(
            persona, medium="Telegram", structured_output=structured
        )
        assert "Vision-inspired reflective wisdom" in contract
        assert "Ultron-inspired incisive analysis" in contract
        assert "not a model upgrade" in contract
        assert "warm French identity" in contract
        if structured:
            assert "machine-readable structure with no extra prose" in contract
