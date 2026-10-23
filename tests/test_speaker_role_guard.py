import pytest
from services.trained_voice.voice_persona import is_speaker_role_reversal


@pytest.mark.parametrize(
    "question",
    [
        "Which version did I ask you to change?",
        "What did I tell you to do?",
        "Which revision have I requested?",
    ],
)
def test_explicit_request_sender_inversion_is_flagged(question):
    assert is_speaker_role_reversal("I asked you to change the second draft.", question)
    assert not is_speaker_role_reversal(
        "You asked me to change the second draft.", question
    )


def test_does_not_rewrite_genuine_assistant_question_recollection():
    assert not is_speaker_role_reversal(
        "I asked you which file you meant.", "What did you ask me?"
    )


def test_last_resort_quotes_the_real_owner_request_not_a_canned_answer():
    from services.trained_voice.voice_persona import grounded_owner_request_quote

    actual = "Keep the green version, remove the footer."
    assert actual in grounded_owner_request_quote(
        [{"user": actual, "assistant": "Done"}]
    )
    assert grounded_owner_request_quote([]) is None


def test_english_wrapper_cannot_hide_a_french_draft():
    from services.trained_voice.voice_persona import needs_english_rewrite

    reply = "Here is the text you asked for:\nA: Je m'excuse pour être en retard, Mina.\nThis has not been sent. Let me know if you want changes."
    assert needs_english_rewrite(reply, "Draft a note to Mina")
