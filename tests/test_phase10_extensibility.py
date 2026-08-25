import sqlite3

from agent.response_planner import plan_response
from agent.tooling import get_runtime_registry
from contracts import CONTRACT_VERSION, contract_catalog, discover_capabilities
from memory.schema_migrations import (
    apply_migrations,
    current_version,
    rollback_migrations,
)
from scripts.apply_migrations import migration_files
from scripts.check_release import validate_release


def test_public_contract_catalog_is_versioned_and_matches_runtime_shapes():
    catalog = contract_catalog()
    assert catalog["contract_version"] == CONTRACT_VERSION
    assert set(catalog) >= {
        "connector",
        "tool",
        "media",
        "memory",
        "voice",
        "response_policy",
    }
    plan = plan_response("Bonjour")
    assert set(catalog["response_policy"]["required_dimensions"]) <= set(plan)
    assert set(catalog["media"]["connectors"]) >= {
        "telegram",
        "discord",
        "whatsapp",
        "slack",
        "api",
    }


def test_every_registered_tool_conforms_to_public_contract():
    required = set(contract_catalog()["tool"]["required_definition_fields"])
    for capability in get_runtime_registry().all():
        assert required <= set(capability.__dataclass_fields__)
        assert capability.risk in contract_catalog()["tool"]["risk_values"]


def test_capability_discovery_hides_unhealthy_features_by_default():
    report = discover_capabilities()
    assert report["contract_version"] == CONTRACT_VERSION
    assert report["capabilities"]
    assert all(item["available"] for item in report["capabilities"])
    assert all("input_schema" not in item for item in report["capabilities"])


def test_sqlite_migrations_support_forward_and_rollback():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE messages(internal_id TEXT, created_at TEXT);"
        "CREATE TABLE action_audit(internal_id TEXT, created_at TEXT);"
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);"
    )
    assert apply_migrations(connection) == 1
    assert current_version(connection) == 1
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_messages_owner_time" in indexes
    assert rollback_migrations(connection, 0) == 0
    assert apply_migrations(connection, 1) == 1


def test_postgres_migrations_are_paired_and_release_evidence_is_complete():
    for _, _, up_path in migration_files():
        assert up_path.with_name(up_path.name.replace(".up.sql", ".down.sql")).is_file()
    manifest = validate_release()
    assert manifest["contract_version"] == CONTRACT_VERSION
