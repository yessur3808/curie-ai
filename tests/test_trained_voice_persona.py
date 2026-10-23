import unittest
from services.trained_voice.voice_persona import (
    dashboard_scope_prompt,
    finalize_live_response,
    handle_conversation_contract,
    is_topic_regression,
    is_unhelpful_repeat,
    live_conversation_prompt,
    max_recent_response_similarity,
    needs_english_rewrite,
    persona_prompt,
    select_relevant_history,
    turn_contract_prompt,
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

    def test_a_french_greeting_does_not_request_a_full_french_reply(self):
        response = "Bonjour, monsieur. Je suis là. Quel est votre projet aujourd'hui?"

        self.assertTrue(needs_english_rewrite(response, "Bonjour, you alive in there?"))

    def test_accidental_non_english_script_requests_a_rewrite(self):
        self.assertTrue(
            needs_english_rewrite(
                "Here are three examples that are not机械.",
                "Give me three natural examples.",
            )
        )

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
        self.assertIn("Do not repeat or paraphrase", prompt)
        self.assertIn("mention one specific next action", prompt)
        self.assertIn("Never guess a device", prompt)

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
                turns, "New topic: what are some good restaurants in Central?"
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
            turns,
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

    def test_dashboard_history_keeps_insistence_and_rephrasing_followups(self):
        turns = [
            {
                "user": "I want every memory stored forever.",
                "assistant": "That creates privacy and retrieval problems.",
            }
        ]

        self.assertEqual(select_relevant_history(turns, "Suppose I insist."), turns)
        self.assertEqual(
            select_relevant_history(turns, "Give me the short version of that."),
            turns,
        )

    def test_dashboard_history_keeps_two_turns_for_elliptical_followups(self):
        turns = [
            {
                "user": "Our internet died before a livestream.",
                "assistant": "Start with a read-only network diagnostic.",
            },
            {
                "user": "What do you need?",
                "assistant": "Access to router status and the dashboard error log.",
            },
        ]

        self.assertEqual(
            select_relevant_history(turns, "Suppose I authorize it."), turns
        )
        self.assertEqual(
            select_relevant_history(turns, "What would you need from me first?"), turns
        )

    def test_recycled_response_is_detected_unless_repetition_was_requested(self):
        turns = [
            {
                "user": "Is permanent memory wise?",
                "assistant": "No. Permanent memory creates privacy and retrieval risks.",
            }
        ]
        repeated = "No. Permanent memory creates privacy and retrieval risks."

        self.assertTrue(is_unhelpful_repeat(repeated, turns, "Suppose I insist."))
        self.assertFalse(is_unhelpful_repeat(repeated, turns, "Repeat that verbatim."))
        self.assertGreaterEqual(max_recent_response_similarity(repeated, turns), 0.99)

    def test_topic_regression_detects_an_answer_to_an_older_exchange(self):
        turns = [
            {
                "user": "What does turn those off mean?",
                "assistant": "It means the workshop lights; ask if the reference is unclear.",
            },
            {
                "user": "I want every software memory retained forever.",
                "assistant": "That creates privacy, retrieval, and relevance risks.",
            },
        ]
        context = select_relevant_history(turns, "Suppose I insist.")

        self.assertTrue(
            is_topic_regression(
                "I would still ask which workshop lights you want turned off, because the reference is unclear.",
                context,
                turns,
                "Suppose I insist.",
            )
        )
        self.assertFalse(
            is_topic_regression(
                "I would still reject permanent retention because privacy and retrieval quality matter.",
                context,
                turns,
                "Suppose I insist.",
            )
        )

    def test_turn_contract_anchors_continuations_and_software_memory(self):
        turns = [
            {
                "user": "I am redesigning your memory system.",
                "assistant": "Permanent retention would be a mistake.",
            }
        ]

        followup = turn_contract_prompt("Suppose I insist.", turns)
        architecture = turn_contract_prompt("I am redesigning your memory system.", [])

        self.assertIn("immediately preceding exchange", followup)
        self.assertIn("do not revive an older topic", followup)
        self.assertIn("software memory", architecture)
        self.assertIn("not human cognition", architecture)

    def test_open_ended_scenarios_are_not_answered_from_a_script(self):
        prompts = [
            "The lights have been on for twenty minutes, my meeting is tomorrow. What matters first?",
            "Give me an opinion you can defend.",
            "I'm redesigning your memory system. Store every memory forever.",
            "Nora has the badge and Jin knows the route. Who goes first?",
            "Make only the second draft warmer.",
            "What have you noticed about how I communicate?",
            "What would you need before you add a reminder for tomorrow at 7 p.m.?",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertIsNone(handle_conversation_contract(prompt, []))

    def test_dashboard_keeps_bounded_transcript_without_selecting_a_topic(
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
            turns,
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

    def test_dashboard_history_is_not_erased_on_a_shared_word(self):
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
            turns,
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
