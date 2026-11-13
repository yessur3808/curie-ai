from agent.orchestration.response_policy import ResponsePolicy


class IdentityStyle:
    def apply_response_style(self, text, *args, **kwargs):
        return text


def test_draft_preserves_requested_details_even_when_repeated_from_user():
    response = "Our fictional review starts at 14:30 UTC on Tuesday. Please adjust your schedules accordingly for this upcoming session."
    user = "Draft a professional note saying our fictional review starts at 14:30 UTC on Tuesday. Do not send anything. At most 20 words."
    assert (
        ResponsePolicy({"name": "Curie"}, IdentityStyle()).finalize(response, user)
        == response
    )
