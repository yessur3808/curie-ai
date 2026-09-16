from memory.adaptive import capture_proactive_feedback


def test_rejected_topic_is_persisted(monkeypatch):
    stored = []
    monkeypatch.setattr(
        "memory.users.UserManager.get_user_profile",
        lambda _user: {"proactive_avoid_topics": ["sports"]},
    )
    monkeypatch.setattr(
        "memory.users.UserManager.update_user_profile",
        lambda user, values: stored.append((user, values)),
    )
    updates = capture_proactive_feedback(
        "u1", "There is no need to keep sharing sky observations"
    )
    assert updates == {
        "proactive_avoid_topics": ["sports", "sky observations"],
        "proactive_rejection_count": 1,
    }
    assert stored == [("u1", updates)]


def test_proactive_opt_out_is_persisted(monkeypatch):
    stored = []
    monkeypatch.setattr(
        "memory.users.UserManager.update_user_profile",
        lambda user, values: stored.append((user, values)),
    )
    updates = capture_proactive_feedback("u1", "Please turn off proactive messages")
    assert updates["proactive_messaging_enabled"] is False
    assert updates["proactive_rejection_count"] == 1


def test_fact_correction_rejects_the_awaiting_proactive_topic(monkeypatch):
    stored = []
    monkeypatch.setattr(
        "memory.users.UserManager.get_user_profile",
        lambda _user: {
            "proactive_awaiting_response": True,
            "proactive_last_topic": "a project idea or creative possibility",
            "proactive_avoid_topics": [],
        },
    )
    monkeypatch.setattr(
        "memory.users.UserManager.update_user_profile",
        lambda user, values: stored.append((user, values)),
    )

    updates = capture_proactive_feedback("u1", "There is no project")

    assert updates["proactive_avoid_topics"] == [
        "a project idea or creative possibility"
    ]
    assert updates["proactive_rejection_count"] == 1
    assert stored == [("u1", updates)]
