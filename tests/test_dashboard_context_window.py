from services.trained_voice.voice_persona import select_relevant_history


def test_reference_to_version_keeps_the_actual_drafts():
    turns = [
        {"user": "Draft A and B to Mina", "assistant": "A: Sorry. B: Thanks."},
        {"user": "Make only the second warmer", "assistant": "B: Thank you, Mina."},
    ]
    assert (
        select_relevant_history(turns, "Which version did I ask you to change?")
        == turns
    )


def test_incidental_shared_words_cannot_resurrect_a_previous_topic():
    turns = [
        {"user": "I'm late", "assistant": "Draft: I'm sorry, Mina."},
        {
            "user": "New topic: X returned HTTP 402 credits depleted",
            "assistant": "The API has no credits.",
        },
    ]
    assert (
        select_relevant_history(turns, "Does that prove I'm logged out?") == turns[-1:]
    )


def test_recent_window_is_bounded_and_keeps_chronological_order():
    turns = [{"user": str(i), "assistant": str(i)} for i in range(12)]
    assert (
        select_relevant_history(turns, "What happened next?", max_turns=3) == turns[-3:]
    )
