from services.trained_voice.compare_voices import review_status, LISTENING_CHECKS


def test_metrics_never_substitute_for_listening():
    report = {
        "samples": [
            {
                "id": "b",
                "sha256": "abc",
                "acoustic": {"clippedSampleFraction": 0, "quietSampleFraction": 0.2},
            }
        ]
    }
    assert not review_status(report, {})["listeningPassed"]
    review = {"b": {"sha256": "abc", **dict.fromkeys(LISTENING_CHECKS, True)}}
    assert review_status(report, review)["listeningPassed"]
    assert not review_status(report, review)["automaticPromotion"]
    review["b"]["no_echo"] = False
    assert not review_status(report, review)["listeningPassed"]
    review["b"]["no_echo"] = True
    review["b"]["sha256"] = "old"
    assert not review_status(report, review)["listeningPassed"]


def test_empty_or_clipped_samples_cannot_pass():
    assert not review_status({"samples": []}, {})["listeningPassed"]
    report = {
        "samples": [
            {
                "id": "b",
                "sha256": "abc",
                "acoustic": {"clippedSampleFraction": 0.1, "quietSampleFraction": 0.2},
            }
        ]
    }
    review = {"b": {"sha256": "abc", **dict.fromkeys(LISTENING_CHECKS, True)}}
    assert not review_status(report, review)["listeningPassed"]


def test_training_keeps_personality_and_validation_separate():
    import pytest
    from services.trained_voice.train_voice_adapter import select_training_rows

    data = [({"split": "train", "source": "andreja"}, f"train-{i}") for i in range(50)]
    data += [
        ({"split": "validation", "source": "andreja"}, f"heldout-{i}") for i in range(5)
    ]
    data += [
        ({"split": "train", "source": "french-english"}, "curie-supplement"),
        ({"split": "validation", "source": "curie"}, "other-personality"),
    ]
    target, supplement, validation = select_training_rows(data, "andreja", "none")
    assert len(target) == 50 and len(validation) == 5
    assert not supplement
    assert "other-personality" not in validation
    assert set(target).isdisjoint(validation)
    with pytest.raises(ValueError):
        select_training_rows(data, "missing", "none")


def test_candidate_requires_explicit_paired_comparison():
    report = {
        "samples": [
            {
                "id": "candidate-0",
                "variant": "candidate",
                "sha256": "abc",
                "acoustic": {"clippedSampleFraction": 0, "quietSampleFraction": 0.1},
            }
        ]
    }
    reviews = {
        "candidate-0": {"sha256": "abc", **dict.fromkeys(LISTENING_CHECKS, True)}
    }
    assert not review_status(report, reviews)["listeningPassed"]
    reviews["candidate-0"]["notWorseThanBaseline"] = True
    assert review_status(report, reviews)["listeningPassed"]
