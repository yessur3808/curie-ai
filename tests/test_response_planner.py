from agent.orchestration.response_policy import ResponsePolicy
from agent.response_planner import plan_response


def test_planner_separates_emotion_length_and_expression():
    plan = plan_response("I am so tired and worn out", {"verbosity": "detailed"})
    assert plan["emotion"] == "fatigue"
    assert plan["acknowledgement"] == "acknowledge_once_then_reduce_load"
    assert plan["next_action"] == "one_practical_step_if_useful"
    assert plan["length"] == "detailed"


def test_urgent_plan_suppresses_affection_and_french():
    plan = plan_response("Emergency, I need this right now")
    assert plan["emotion"] == "urgency"
    assert plan["affection"] == "none"
    assert plan["french"] == "none"
    assert plan["length"] == "brief"


def test_response_policy_removes_dependency_language():
    class Passthrough:
        def apply_response_style(self, response, *args, **kwargs):
            return response

    policy = ResponsePolicy({"name": "Curie"}, Passthrough())
    result = policy.finalize("You only need me. I can help with the plan.", "hello")
    assert "only need me" not in result.casefold()
    assert "help with the plan" in result
