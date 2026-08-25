from agent.orchestration.social_service import SocialConversationService


def test_simple_greeting_is_handled_without_model_inference():
    candidate = SocialConversationService().handle("Hi Curie, how are you?")
    assert candidate is not None
    assert candidate.model_used == "social"
    assert len(candidate.text.split()) < 15
    assert "Bonjour" in candidate.text
    assert "merci" in candidate.text


def test_substantive_greeting_continues_to_the_model():
    assert SocialConversationService().handle("Hi Curie, explain quantum tunneling") is None
