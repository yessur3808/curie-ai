from unittest.mock import patch

from agent.chat_workflow import ChatWorkflow
from memory import local_store


class _SessionMetadata:
    def __init__(self):
        self.rows = {}

    def get_metadata(self, platform, internal_id):
        return dict(self.rows.get((platform, internal_id), {}))

    def set_metadata(self, platform, internal_id, key, value):
        self.rows.setdefault((platform, internal_id), {})[key] = value


def _history(count=24):
    return [
        ("user" if index % 2 == 0 else "assistant", f"message {index}")
        for index in range(count)
    ]


def _workflow():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    # Deployment settings may raise the threshold through .env. These tests
    # exercise compaction itself with a stable, intentionally small boundary.
    workflow._HISTORY_SUMMARISE_THRESHOLD = 20
    workflow._HISTORY_KEEP_RECENT = 6
    return workflow


def test_rolling_summary_is_persisted_and_reused_without_reinference():
    metadata = _SessionMetadata()
    history = _history()
    with (
        patch("agent.chat_workflow.get_session_manager", return_value=metadata),
        patch(
            "llm.providers.ask_best_provider",
            return_value="An active project remains open; the earlier side topic is resolved.",
        ) as summarize,
    ):
        first = _workflow()._maybe_summarise_history(
            history, platform="telegram", internal_id="owner-1"
        )
        second = _workflow()._maybe_summarise_history(
            history, platform="telegram", internal_id="owner-1"
        )

    assert summarize.call_count == 1
    assert first == second
    assert first[0][0] == "system"
    assert "background only" in first[0][1]
    assert "Never continue" in first[0][1]
    stored = metadata.rows[("telegram", "owner-1")]["working_context_v1"]
    assert stored["version"] == 1
    assert stored["covered_tail_fingerprint"]


def test_rolling_summary_incrementally_folds_only_newly_aged_turns():
    metadata = _SessionMetadata()
    prompts = []

    def summarize(prompt, **_kwargs):
        prompts.append(prompt)
        return "summary one" if len(prompts) == 1 else "summary two"

    workflow = _workflow()
    history = _history()
    with (
        patch("agent.chat_workflow.get_session_manager", return_value=metadata),
        patch("llm.providers.ask_best_provider", side_effect=summarize),
    ):
        workflow._maybe_summarise_history(
            history, platform="telegram", internal_id="owner-1"
        )
        result = workflow._maybe_summarise_history(
            [*history, ("user", "a new request"), ("assistant", "a new answer")],
            platform="telegram",
            internal_id="owner-1",
        )

    assert len(prompts) == 2
    assert "Previous model-generated summary" in prompts[1]
    assert "summary one" in prompts[1]
    assert "message 0" not in prompts[1]
    assert result[0][0] == "system"
    assert "summary two" in result[0][1]


def test_local_session_metadata_is_owner_scoped_and_reset_clears_only_summary(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    manager = local_store.LocalSessionManager()
    manager.set_metadata(
        "telegram", "owner-1", "working_context_v1", {"summary": "one"}
    )
    manager.set_metadata("telegram", "owner-1", "theme", "dark")
    manager.set_metadata(
        "telegram", "owner-2", "working_context_v1", {"summary": "two"}
    )

    assert manager.get_metadata("telegram", "owner-1")["working_context_v1"] == {
        "summary": "one"
    }
    assert manager.get_metadata("telegram", "owner-2")["working_context_v1"] == {
        "summary": "two"
    }

    manager.reset_session("telegram", "owner-1")

    assert manager.get_metadata("telegram", "owner-1") == {"theme": "dark"}
    assert manager.get_metadata("telegram", "owner-2")["working_context_v1"] == {
        "summary": "two"
    }
