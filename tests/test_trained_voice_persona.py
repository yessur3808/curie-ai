import unittest
from services.trained_voice.voice_persona import (
    dashboard_scope_prompt,
    finalize_live_response,
    live_conversation_prompt,
    needs_english_rewrite,
    persona_prompt,
    select_relevant_history,
    delivery_settings,
    persona_revision,
)


class PersonaTests(unittest.TestCase):
    def test_dashboard_rewrites_accidental_broken_french(self):
        response = (
            "Bonjour, monsieur. Je bien sûr que je suis en forme, merci de vous "
            "demander ça."
        )

        self.assertTrue(needs_english_rewrite(response, "How are you today?"))
        self.assertTrue(
            needs_english_rewrite(
                "Bonjour! Je vais bien, merci. Comment vas-tu?", "How are you today?"
            )
        )
        self.assertFalse(needs_english_rewrite(response, "Reply to me in French"))

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
        self.assertIn("do not compose full French clauses", prompt)

    def test_dashboard_scope_does_not_infer_an_outage_without_a_snapshot(self):
        prompt = dashboard_scope_prompt(False)

        self.assertIn("not evidence that anything is offline", prompt)
        self.assertIn("otherwise omit all snapshot and status talk", prompt)

    def test_dashboard_scope_allows_relevant_supplied_snapshot(self):
        prompt = dashboard_scope_prompt(True)

        self.assertIn("snapshot is supplied", prompt)
        self.assertIn("only when it is directly relevant", prompt)
        self.assertIn("newest message defines the active subject", prompt)

    def test_dashboard_history_drops_an_unrelated_old_topic(self):
        turns = [
            {
                "user": "Explain the GPU memory problem in Curie.",
                "assistant": "The voice and chat models are competing for VRAM.",
            }
        ]

        self.assertEqual(
            select_relevant_history(
                turns, "What are some good restaurants in Central?"
            ),
            [],
        )

    def test_dashboard_history_keeps_a_real_follow_up(self):
        turns = [
            {
                "user": "Would Rust help the memory kernel?",
                "assistant": "Yes, especially for retrieval and ranking.",
            },
            {
                "user": "How is the weather?",
                "assistant": "Warm with a chance of rain.",
            },
        ]

        self.assertEqual(
            select_relevant_history(turns, "What about the Rust ranking path?"),
            turns[:1],
        )

    def test_dashboard_history_keeps_a_short_answer_to_curie_question(self):
        turns = [
            {
                "user": "Help me plan a trip.",
                "assistant": "Where are you going?",
            }
        ]

        self.assertEqual(select_relevant_history(turns, "Los Angeles"), turns)

    def test_dashboard_history_keeps_a_short_pronoun_follow_up(self):
        turns = [
            {
                "user": "The response cache uses too much memory.",
                "assistant": "We can reduce its capacity or shorten its TTL.",
            }
        ]

        self.assertEqual(select_relevant_history(turns, "Would that help?"), turns)

    def test_dashboard_history_does_not_mistake_self_contained_this_for_a_reference(
        self,
    ):
        turns = [
            {
                "user": "Tell me about Rust memory safety.",
                "assistant": "Ownership prevents several classes of memory errors.",
            }
        ]

        self.assertEqual(
            select_relevant_history(
                turns, "What is this year's inflation rate in Hong Kong?"
            ),
            [],
        )

    def test_dashboard_history_honors_an_explicit_topic_change(self):
        turns = [
            {
                "user": "Tell me about Rust memory safety.",
                "assistant": "Ownership prevents several classes of memory errors.",
            }
        ]

        self.assertEqual(
            select_relevant_history(
                turns, "Different topic: is memory foam good for side sleepers?"
            ),
            [],
        )

    def test_dashboard_history_does_not_follow_one_ambiguous_shared_word(self):
        turns = [
            {
                "user": "Tell me about Rust memory safety.",
                "assistant": "Ownership prevents several classes of memory errors.",
            }
        ]

        self.assertEqual(
            select_relevant_history(
                turns, "Is memory foam good for people who sleep on their side?"
            ),
            [],
        )

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
