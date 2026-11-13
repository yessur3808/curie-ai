import pytest

from memory import local_store
from memory.repositories import get_repositories, reset_repositories

pytestmark = pytest.mark.security


def test_sqlite_repositories_share_identical_runtime_contracts(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    reset_repositories()
    repositories = get_repositories()

    internal_id = repositories.identities.get_or_create("telegram", "42")
    assert repositories.identities.get_external_id(internal_id, "telegram") == "42"

    repositories.profiles.update(internal_id, {"name": "Yaser"})
    assert repositories.profiles.get(internal_id)["name"] == "Yaser"
    assert repositories.profiles.list_with_identities()[0]["internal_id"] == internal_id

    repositories.sessions.add_message("telegram", internal_id, "user", "hello")
    assert (
        repositories.sessions.get_history("telegram", internal_id)[0]["content"]
        == "hello"
    )
    repositories.sessions.reset_session("telegram", internal_id)
    assert repositories.sessions.get_history("telegram", internal_id) == []

    token = repositories.approvals.create(internal_id, {"action": "demo"})
    assert repositories.approvals.consume("someone-else", token, True) is None
    assert repositories.approvals.consume(internal_id, token, True) == {
        "action": "demo"
    }
    assert repositories.approvals.consume(internal_id, token, True) is None

    repositories.audits.append(internal_id, "demo", "completed", {"ok": True})
    audit = repositories.audits.list(internal_id)
    assert audit[0]["action"] == "demo"
    assert audit[0]["details"]["schema_version"] == 1
    assert audit[0]["details"]["parameters"] == {}
    assert audit[0]["details"]["integrity_hash"]
    assert repositories.backend == "sqlite"


def test_repository_factory_selects_external_only_with_both_databases(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.delenv("MONGODB_URI", raising=False)
    reset_repositories()
    assert get_repositories().backend == "sqlite"


@pytest.mark.parametrize("approve", [True, False])
def test_external_approval_claim_filters_connector_atomically(monkeypatch, approve):
    from types import SimpleNamespace
    from memory import database
    from memory.repositories import ExternalApprovalRepository

    calls = []

    def claim(query, update, **kwargs):
        calls.append((query, update))
        return None

    monkeypatch.setattr(
        database,
        "mongo_db",
        SimpleNamespace(pending_actions=SimpleNamespace(find_one_and_update=claim)),
    )
    assert (
        ExternalApprovalRepository().consume(
            "owner", "ABCDEF12", approve, connector="telegram"
        )
        is None
    )
    query, update = calls.pop()
    assert query["action.authorization.connector"] == "telegram"
    assert query["internal_id"] == "owner"
    assert query["token"] == "abcdef12"
    assert query["status"] == "pending"
    assert "$gt" in query["expires_at"]
    assert update == {"$set": {"status": "approved" if approve else "rejected"}}
