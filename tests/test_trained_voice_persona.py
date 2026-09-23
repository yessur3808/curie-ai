import unittest
from services.trained_voice.voice_persona import (
    dashboard_scope_prompt,
    finalize_live_response,
    live_conversation_prompt,
    persona_prompt,
    delivery_settings,
    persona_revision,
)


class PersonaTests(unittest.TestCase):
    def test_briefing_retains_actual_profile(self):
        p = {
            "system_prompt": "Unique complete Curie personality.",
            "response_style": {"cadence": "measured French rhythm"},
            "language_profile": {"primary_language": "english"},
        }
        prompt = persona_prompt(p, briefing=True)
        self.assertIn(p["system_prompt"], prompt)
        self.assertIn("measured French rhythm", prompt)
        self.assertIn("Preserve supplied names, times, quantities", prompt)
        self.assertIn("normally spelled English", prompt)

    def test_dashboard_scope_does_not_infer_an_outage_without_a_snapshot(self):
        prompt = dashboard_scope_prompt(False)

        self.assertIn("not evidence that anything is offline", prompt)
        self.assertIn("otherwise omit all snapshot and status talk", prompt)

    def test_dashboard_scope_allows_relevant_supplied_snapshot(self):
        prompt = dashboard_scope_prompt(True)

        self.assertIn("snapshot is supplied", prompt)
        self.assertIn("only when it is directly relevant", prompt)

    def test_live_conversation_does_not_append_an_unasked_question_or_status(self):
        prompt = live_conversation_prompt()

        self.assertIn("Do not append a question", prompt)
        self.assertIn("status recap", prompt)
        self.assertIn("current message asks for it", prompt)
        self.assertIn("under 60 words", prompt)

    def test_live_finalizer_removes_unasked_follow_up_question(self):
        response = "Bonjour. I'm doing well, thank you for asking. How about you?"

        self.assertEqual(
            finalize_live_response(response, "How are you?", has_snapshot=False),
            "Bonjour. I'm doing well, thank you for asking.",
        )

    def test_live_finalizer_removes_unrequested_snapshot_status(self):
        response = (
            "You asked me to keep it casual. I have no snapshot available, so there is "
            "nothing to report on that front."
        )

        self.assertEqual(
            finalize_live_response(
                response, "What did I just ask?", has_snapshot=False
            ),
            "You asked me to keep it casual.",
        )

    def test_live_finalizer_keeps_explicitly_requested_question(self):
        response = "All right. What would you like to build?"

        self.assertEqual(
            finalize_live_response(
                response, "Ask me a question about my project", has_snapshot=False
            ),
            response,
        )

    def test_live_finalizer_removes_markdown_emphasis_from_spoken_text(self):
        response = "You asked how I was doing. *Oui*, that is the gist of it."

        self.assertEqual(
            finalize_live_response(response, "What did I ask?", has_snapshot=False),
            "You asked how I was doing. Oui, that is the gist of it.",
        )

    def test_context_changes_delivery_and_profile_revision(self):
        p = {"response_style": {"cadence": "measured"}}
        urgent = delivery_settings(p, "urgent")
        emotional = delivery_settings(p, "emotional")
        self.assertLess(urgent["pause"], emotional["pause"])
        self.assertGreater(urgent["rate"], emotional["rate"])
        self.assertNotEqual(
            persona_revision(p),
            persona_revision({"response_style": {"cadence": "natural"}}),
        )


if __name__ == "__main__":
    unittest.main()
