import unittest
from services.trained_voice.voice_text import speech_chunks


class SpeechChunkTests(unittest.TestCase):
    def test_long_report_keeps_every_word_and_bounds_chunks(self):
        text = (
            "Hong Kong humidity is 81.5 percent. BTC is available in HKD and USD. "
            * 150
        ).strip()
        chunks = speech_chunks(text)
        self.assertEqual(" ".join(chunks), text)
        self.assertTrue(all(0 < len(x) <= 240 for x in chunks))

    def test_markdown_and_urls_are_not_spoken(self):
        self.assertEqual(
            speech_chunks("## Projects\n• Visit https://example.com today."),
            ["Projects Visit today."],
        )

    def test_trained_voice_uses_short_sentences_without_splitting_titles(self):
        text = "Bonjour, Yaser. I am here. Dr. Smith arrives at three thirty. The server is healthy today."
        chunks = speech_chunks(text, limit=180, sentence_only=True)
        self.assertEqual(" ".join(chunks), text)
        self.assertEqual(chunks[0], "Bonjour, Yaser. I am here.")
        self.assertEqual(chunks[1], "Dr. Smith arrives at three thirty.")

    def test_empty_or_unbounded_words_fail(self):
        for text in ["", "https://example.com", "x" * 241]:
            with self.assertRaises(ValueError):
                speech_chunks(text)

    def test_report_lines_keep_separate_speech_boundaries(self):
        text = "• Thunderstorm warning — Official warning · Hong Kong Observatory\n• Project: deployment unavailable — Provider returned HTTP 404."
        self.assertEqual(
            speech_chunks(text, limit=180, sentence_only=True),
            [
                "Thunderstorm warning, Official warning, Hong Kong Observatory.",
                "Project: deployment unavailable, Provider returned HTTP 404.",
            ],
        )


if __name__ == "__main__":
    unittest.main()
