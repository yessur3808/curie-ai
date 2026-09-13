from agent.orchestration.social_service import SocialConversationService


def test_relational_greeting_continues_to_model_with_history():
    assert SocialConversationService().handle("Hi Curie, how are you?") is None


def test_bare_greeting_stays_fast_and_deterministic():
    candidate = SocialConversationService().handle("Hi Curie")
    assert candidate is not None
    assert candidate.model_used == "social"
    assert "Bonjour" in candidate.text


def test_substantive_greeting_continues_to_the_model():
    assert SocialConversationService().handle("Hi Curie, explain quantum tunneling") is None
