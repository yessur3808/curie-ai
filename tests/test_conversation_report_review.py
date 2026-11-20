from copy import deepcopy

from evaluation.review_conversation_reports import review_report


def test_rescores_original_evidence_without_trusting_old_checks_or_mutating_it():
    report = {
        "complete": True,
        "transcript": [
            {
                "flow": "voice_holdout",
                "turn": 4,
                "assistant": "Practical and efficient.",
                "tool_calls": [],
                "behavioral_checks": {"has_answer": True},
            }
        ],
    }
    before = deepcopy(report)
    summary = review_report(report)
    assert not summary["passed"]
    assert summary["failures"][0]["checks"] == ["no_stock_preference_praise"]
    assert report == before


def test_empty_or_partial_reports_never_pass():
    assert not review_report({"complete": True, "transcript": []})["passed"]
    assert not review_report(
        {
            "complete": False,
            "transcript": [
                {
                    "flow": "voice_holdout",
                    "turn": 2,
                    "assistant": "All right.",
                    "tool_calls": [],
                }
            ],
        }
    )["passed"]
