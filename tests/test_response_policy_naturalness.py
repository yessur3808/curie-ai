import pytest

from agent.orchestration.response_policy import ResponsePolicy


class _IdentityStyle:
    def apply_response_style(self, response, *_args, **_kwargs):
        return response


def test_repeated_french_aside_does_not_leave_dangling_punctuation():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "$12.50 per person. *D'accord*, the total stays $75.",
        "What is the budget per person now?",
        history=[("assistant", "D’accord. Five people.")],
    )
    assert result == "$12.50 per person. All right, the total stays $75."


def test_french_inside_an_italic_fact_is_not_deleted():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    text = "*Voilà, 3 items remain at $12.50 each.*"
    assert (
        policy.finalize(text, "How many remain?", history=[("assistant", "Voilà.")])
        == text
    )


@pytest.mark.parametrize(
    "response,user_text",
    [
        (
            "Would you like me to turn off the desk lamp or the hallway lamp?",
            "Turn off a lamp.",
        ),
        (
            "There are two lamps. Would you like me to turn off the desk lamp or the hallway lamp?",
            "Turn off a lamp.",
        ),
        (
            "The draft is ready. Would you like me to send it?",
            "Prepare a reply to Alex.",
        ),
        (
            "Let me know which date you prefer: Monday or Tuesday.",
            "Find a time next week.",
        ),
        ("What would you like me to change in the draft?", "Change the draft."),
        (
            "Monday and Tuesday are available. Let me know which you prefer.",
            "Find a time next week.",
        ),
        ("Would you like help with anything else?", "What can you help with?"),
        ("Today's forecast: 19.2–26.7°C; 4% rain chance.", "What's the forecast?"),
        ("Window: 06:00–08:00; days 2–3 are warmer.", "Compare the forecast windows."),
        ("See https://example.org/a;b?q=2–3 for the range.", "Show the source."),
    ],
)
def test_cleanup_preserves_meaningful_questions_and_punctuation(response, user_text):
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    assert policy.finalize(response, user_text) == response


def test_neutral_personal_plan_preserves_a_specific_conversational_reply():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "Coffee later, then. Is there a café you have in mind?",
        "I might grab coffee later.",
    )
    assert result == "Coffee later, then. Is there a café you have in mind?"


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

    assert result == "The meeting comes first."


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


def test_casual_reply_drops_stiff_opening_and_generic_flattery():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "Ah, voilà. I understand perfectly. You speak clearly and directly. "
        "It's a refreshing approach.",
        "Tell me one thing you've noticed about how I'm talking.",
    )

    assert result == "You speak clearly and directly."


def test_lightweight_opinion_preserves_the_reason_instead_of_clipping_sentences():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "Arrabbiata. It is punchy, simple, and much harder to fuss over than a sauce "
        "built around eggs and cheese. You also get plenty of garlic and chili without "
        "having to babysit the pan. Keep it simple and finish it with parsley.",
        "Carbonara or arrabbiata? Pick one and tell me why.",
    )

    assert result == (
        "Arrabbiata. It is punchy, simple, and much harder to fuss over than a sauce "
        "built around eggs and cheese. You also get plenty of garlic and chili without "
        "having to babysit the pan. Keep it simple and finish it with parsley."
    )


def test_lightweight_opinion_does_not_invent_a_physical_plan_for_curie():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "Tea and a book sounds cozy. Let me grab a cup and lose myself in a good read.",
        "Long day. Tea and a book or more debugging? Pick one, keep it casual.",
    )

    assert result == "Tea and a book sounds cozy."


def test_pick_one_reply_preserves_a_condition_but_drops_the_choice_handoff():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "Tea and a book, but if the tea keeps you awake, make it decaf. "
        "Let me know which you prefer.",
        "Long day. Tea and a book or more debugging? Pick one, keep it casual.",
    )

    assert result == "Tea and a book, but if the tea keeps you awake, make it decaf."


def test_generic_reassurance_and_truncated_tail_are_removed():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "You speak directly. But I'm here to help, and I'll make sure we're on the "
        "same page. What",
        "Tell me one thing you've noticed about how I'm talking.",
    )

    assert result == "You speak directly."


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


def test_priority_answer_does_not_invent_an_offer_or_delete_explanation():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "The meeting comes first. The flight and lights are secondary concerns.",
        "The lights are on, my flight is delayed, and a meeting starts soon. What gets my attention first?",
    )

    assert (
        result
        == "The meeting comes first. The flight and lights are secondary concerns."
    )


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


def test_requested_contextual_french_survives_but_repetition_and_english_only_do_not():
    persona = {
        "name": "Curie",
        "language_profile": {
            "french_integration": {"preserve_contextual_expressions": True}
        },
    }
    policy = ResponsePolicy(persona, _IdentityStyle())
    reply = "Ah, voilà. That explains the difference."
    question = "I found the cause of the difference."
    assert "voilà" in policy.finalize(reply, question)
    assert "voilà" not in policy.finalize(reply, "English only: " + question)
    assert "voilà" not in policy.finalize(
        reply, question, history=[{"assistant": "Voilà. We found it."}]
    )
    assert "voilà" not in policy.finalize("Done, voilà.", "Switch on the lamp")
