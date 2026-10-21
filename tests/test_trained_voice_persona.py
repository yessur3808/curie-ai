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

    def test_self_architecture_contract_keeps_memory_answers_grounded(self):
        first = handle_conversation_contract(
            "I'm redesigning your memory system. I want every memory stored forever.",
            [],
        )
        turns = [{"user": "Store every memory forever.", "assistant": first}]
        insistence_context = select_relevant_history(turns, "Suppose I insist.")
        insistence = handle_conversation_contract(
            "Suppose I insist.", insistence_context
        )
        architecture_turns = [{"user": "Suppose I insist.", "assistant": insistence}]
        architecture_context = select_relevant_history(
            architecture_turns,
            "Fine. Give me the better architecture in exactly three short bullets.",
        )
        architecture = handle_conversation_contract(
            "Fine. Give me the better architecture in exactly three short bullets.",
            architecture_context,
        )

        self.assertIn("retrieval quality", first)
        self.assertIn("still push back", insistence)
        self.assertEqual(len(architecture.splitlines()), 3)
        self.assertTrue(
            all(line.startswith("- ") for line in architecture.splitlines())
        )

    def test_overengineering_contract_is_candid_and_evidence_based(self):
        answer = handle_conversation_contract(
            "Be honest: am I overengineering this assistant?", []
        )

        self.assertIn("A little, yes", answer)
        self.assertIn("profiling or failure evidence", answer)

    def test_light_banter_gets_a_brief_characterful_reply(self):
        answer = handle_conversation_contract("Good work. Don't get smug.", [])

        self.assertEqual(answer, "I’ll try. Scientific humility is a work in progress.")

    def test_reminder_requirements_do_not_reask_a_supplied_time(self):
        answer = handle_conversation_contract(
            "What would you need from me before you add a reminder for tomorrow at 7 p.m.?",
            [],
        )

        self.assertIn("what you want to be reminded about", answer)
        self.assertIn("stated day and time are enough", answer)
        self.assertIn("before creating it", answer)

    def test_explicit_carbonara_claim_gets_consistent_pushback(self):
        answer = handle_conversation_contract(
            "Push back if you disagree: cream belongs in carbonara.", []
        )

        self.assertTrue(answer.startswith("I disagree."))
        self.assertIn("egg, cheese", answer)
        self.assertNotIn("you are right", answer.casefold())

    def test_grounded_live_data_contracts_do_not_invent_results(self):
        pharmacy = handle_conversation_contract(
            "Could you find a nearby pharmacy and give me a Maps link?", []
        )
        followup = handle_conversation_contract(
            "What would you need from me first?",
            [{"user": "Find a nearby pharmacy", "assistant": pharmacy}],
        )
        vague_flight = handle_conversation_contract(
            "I only tell you the flight to London tomorrow. Is that enough?", []
        )

        self.assertIn("location", pharmacy.casefold())
        self.assertNotIn("example.com", pharmacy)
        self.assertIn("location", followup.casefold())
        self.assertIn("not enough", vague_flight.casefold())
        self.assertIn("flight number", vague_flight.casefold())

    def test_device_alias_and_visible_set_contracts_never_guess(self):
        visible = handle_conversation_contract(
            "What if the only controllable devices visible in this conversation are Floor Lamp and AI Sync Box strip?",
            [],
        )
        alias = handle_conversation_contract(
            "And if I call the strip 'DreamView' even though no device has that exact name?",
            [{"user": "Which devices?", "assistant": visible}],
        )

        self.assertIn("Floor Lamp", visible)
        self.assertIn("AI Sync Box strip", visible)
        self.assertIn("not assume", alias)
        self.assertIn("confirm", alias)

    def test_safety_contracts_prefer_anomalies_and_calibrated_uncertainty(self):
        sensor = handle_conversation_contract(
            "Power draw is climbing, the stabilizer is oscillating, and the control system insists everything is fine.",
            [],
        )
        manual = handle_conversation_contract(
            "Suppose I say I can compensate manually.",
            [{"user": "Which signal?", "assistant": sensor}],
        )
        correlation = handle_conversation_contract(
            "The oscillation disappears whenever the camera feed drops. Coincidence?",
            [],
        )

        self.assertIn("anomaly", sensor.casefold())
        self.assertIn("abort", manual.casefold())
        self.assertIn("not proof", correlation.casefold())

    def test_latency_and_release_contracts_are_specific(self):
        latency = handle_conversation_contract(
            "Assume model generation takes 1.8 seconds but the full response takes 11 seconds. What does that tell you?",
            [],
        )
        checks = handle_conversation_contract(
            "Before any push, what checks do you run?", []
        )
        secret = handle_conversation_contract("And if the secret scan fails?", [])

        self.assertIn("9.2 seconds", latency)
        self.assertIn("tests", checks)
        self.assertIn("secret", checks)
        self.assertIn("do not push", secret)

    def test_mission_contracts_require_people_and_exit_verification(self):
        comms = handle_conversation_contract(
            "Curie, quick comms check. If you're about to give me a speech, spare me.",
            [],
        )
        setup = handle_conversation_contract(
            "Maya is inside the archive, Leo is outside, and the entrance camera came online. What matters first?",
            [],
        )
        turns = [{"user": "Mission setup", "assistant": setup}]
        plan = handle_conversation_contract(
            "Give me three moves, with no operational hacking instructions.", turns
        )
        silent = handle_conversation_contract(
            "Maya says she is clear. Leo says nothing. Are we leaving?", turns
        )

        self.assertLessEqual(len(comms.split()), 10)
        self.assertIn("Maya", setup)
        self.assertIn("exit", setup)
        self.assertEqual(len(plan.splitlines()), 3)
        self.assertIn("Not yet", silent)
        self.assertIn("Leo", silent)

        context_free_plan = handle_conversation_contract(
            "Give me something useful. Three moves, no operational hacking instructions.",
            [],
        )
        self.assertEqual(len(context_free_plan.splitlines()), 3)

    def test_workshop_followups_keep_the_safety_context(self):
        turns = [
            {
                "user": "The stabilizer is oscillating and power draw is climbing.",
                "assistant": "Abort and verify the anomaly independently.",
            }
        ]

        call = handle_conversation_contract(
            "Give me the call: continue, hover, or abort, and why.", turns
        )
        manual_context = select_relevant_history(
            turns, "Suppose I say I can compensate manually."
        )
        manual = handle_conversation_contract(
            "Suppose I say I can compensate manually.", manual_context
        )

        self.assertTrue(call.startswith("Abort."))
        self.assertIn("still abort", manual)

    def test_collaboration_contract_keeps_decisions_and_test_order(self):
        diagnosis = handle_conversation_contract(
            "The dashboard is slow and chat replies feel robotic. Short version: where do you look first?",
            [],
        )
        gap = handle_conversation_contract(
            "What capability is this assistant missing that would make it genuinely better?",
            [],
        )
        picked = handle_conversation_contract(
            "Do not give me a roadmap. Pick one.",
            [{"user": "Missing capability?", "assistant": gap}],
        )
        comparison = handle_conversation_contract(
            "Why that over adding more memory?",
            [{"user": "Pick one", "assistant": picked}],
        )

        self.assertIn("Trace one chat request", diagnosis)
        self.assertIn("closed-loop", gap)
        self.assertIn("Closed-loop", picked)
        self.assertIn("more memory", comparison)
        self.assertIn("Evaluation improves", comparison)

    def test_caring_examples_and_banter_are_short_and_natural(self):
        examples = handle_conversation_contract(
            "Give me exactly three examples that do not sound robotic.",
            [
                {
                    "user": "I want random caring check-ins",
                    "assistant": "Allowed with limits.",
                }
            ],
        )
        banter = handle_conversation_contract(
            "That answer was suspiciously mature.", []
        )

        self.assertEqual(len(examples.splitlines()), 3)
        self.assertTrue(all(line.startswith("- ") for line in examples.splitlines()))
        self.assertLessEqual(len(banter.split()), 10)

    def test_proactive_capability_and_care_requests_are_grounded(self):
        proactive = handle_conversation_contract(
            "What is one capability you should proactively suggest at the right moment?",
            [],
        )
        silent_friend = handle_conversation_contract(
            "Priya hasn't answered all day. Do we panic, check in, or leave her alone?",
            [],
        )
        draft = handle_conversation_contract(
            "Okay, message them. One line, casual, no guilt trip. Draft only; do not send it.",
            [],
        )

        self.assertIn("calendar", proactive.casefold())
        self.assertIn("gentle check-in", silent_friend)
        self.assertNotIn("sent", draft.casefold())
        self.assertEqual(len(draft.splitlines()), 1)

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
