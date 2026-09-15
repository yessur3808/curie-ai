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

    assert len(contract) < 180
    assert "Curie:" in contract
    assert "curious" in contract
    assert "French" in contract
    assert "dry wit" in contract
