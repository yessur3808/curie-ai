from agent.orchestration.response_policy import ResponsePolicy


class _IdentityStyle:
    def apply_response_style(self, response, *_args, **_kwargs):
        return response


def test_neutral_personal_plan_drops_generic_praise_and_invented_wellness():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "That sounds like a good idea. It might help to clear your head.",
        "I might grab coffee later.",
    )
    assert result == "Fair enough."


def test_neutral_plan_guard_does_not_rewrite_questions_or_other_personas():
    curie = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    jarvis = ResponsePolicy({"name": "Jarvis"}, _IdentityStyle())
    response = "That sounds like a good idea. It might help to clear your head."
    assert curie.finalize(response, "Would coffee help me clear my head?") == response
    assert jarvis.finalize(response, "I might grab coffee later.") == response
