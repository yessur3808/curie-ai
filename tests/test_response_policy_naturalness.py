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


def test_detailed_user_situation_is_not_repeated_before_the_answer():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "Ah, the workshop lights are on, your flight is delayed, and your meeting "
        "starts in twenty minutes. The meeting comes first.",
        "The workshop lights are on, my flight is delayed, and I have a meeting in "
        "twenty minutes. What gets my attention first?",
    )

    assert result == "The meeting comes first. I can check the flight details next."


def test_generic_offer_is_removed_but_specific_proactive_solution_survives():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    generic = policy.finalize(
        "The meeting comes first. Would you like help with anything else?",
        "What gets my attention first?",
    )
    specific = policy.finalize(
        "The meeting comes first. I can check the delayed flight next.",
        "What gets my attention first?",
    )

    assert generic == "The meeting comes first."
    assert specific == "The meeting comes first. I can check the delayed flight next."


def test_dashboard_history_suppresses_repeated_french_and_invented_experience():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "Ah, voilà. Honesty matters more than easy agreement. That's something "
        "I've learned over time.",
        "Give me one opinion.",
        history=[{"user": "Hello", "assistant": "Voilà. I found it."}],
    )

    assert result == "Honesty matters more than easy agreement."


def test_brutally_short_request_drops_an_appended_question():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())

    assert (
        policy.finalize(
            "Good morning. I'm awake. And you?",
            "Morning, Curie. Brutally short version: are you awake?",
        )
        == "Good morning. I'm awake."
    )


def test_exact_bullets_are_split_when_model_returns_one_line():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "- Keep core facts - Expire weak inferences - Review sensitive memories",
        "Give me the better architecture in exactly three short bullets.",
    )

    assert result.splitlines() == [
        "- Keep core facts",
        "- Expire weak inferences",
        "- Review sensitive memories",
    ]


def test_requested_moves_and_inline_star_items_become_real_bullets():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    moves = policy.finalize(
        "1. Confirm Maya's route. 2. Get Leo on comms. 3. Use the safe exit.",
        "Give me something useful. Three moves, no operational hacking instructions.",
    )
    stars = policy.finalize(
        "* Isolate the camera feed * Repeat the test at low power",
        "Design the next safe test in exactly two short bullets.",
    )

    assert moves.splitlines() == [
        "- Confirm Maya's route.",
        "- Get Leo on comms.",
        "- Use the safe exit.",
    ]
    assert stars.splitlines() == [
        "- Isolate the camera feed",
        "- Repeat the test at low power",
    ]


def test_one_line_and_no_congratulations_are_enforced():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    one_line = policy.finalize(
        "Hey, checking in.\nNo pressure to answer right away.",
        "Draft the message. One line. Warm, not creepy.",
    )
    landed = policy.finalize(
        "Congratulations. The rig is intact. Now inspect the telemetry.",
        "I landed. Don't congratulate me.",
    )

    assert one_line == "Hey, checking in. No pressure to answer right away."
    assert "congrat" not in landed.casefold()


def test_generic_anything_else_offer_is_removed():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())

    assert (
        policy.finalize(
            "Thank you. Let me know if there's anything else I can assist with.",
            "Good work. Don't get smug.",
        )
        == "Thank you."
    )


def test_priority_answer_is_decisive_and_proactively_offers_one_real_next_step():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "The meeting comes first. The flight and lights are secondary concerns.",
        "The lights are on, my flight is delayed, and a meeting starts soon. What gets my attention first?",
    )

    assert result == "The meeting comes first. I can check the flight details next."


def test_invented_experience_and_empty_closing_offer_are_removed():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    opinion = policy.finalize(
        "That is an observation based on experience.",
        "Give me one opinion you can defend.",
    )
    closing = policy.finalize(
        "Anytime. I'm here if you need anything else.",
        "That will do.",
    )

    assert opinion == "That is an observation based on my reasoning."
    assert closing == "Anytime."
