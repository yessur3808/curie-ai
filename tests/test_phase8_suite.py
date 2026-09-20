from evaluation.phase8_suite import run


def test_phase8_controlled_learning_release_suite():
    report = run()
    assert report["score"] == 10.0
    assert all(report["checks"].values())
