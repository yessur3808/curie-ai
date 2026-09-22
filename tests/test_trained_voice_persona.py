import unittest
from services.trained_voice.voice_persona import (
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
