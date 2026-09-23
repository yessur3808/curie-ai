"""Blocking contracts for Curie's unified Rust runtime kernel."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zipfile

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.security,
    pytest.mark.skipif(
        importlib.util.find_spec("_curie_runtime_kernel") is None,
        reason="native runtime kernel is not built",
    ),
]


@pytest.fixture()
def runtime(monkeypatch):
    monkeypatch.setenv("CURIE_RUNTIME_KERNEL", "rust")
    from services import runtime_kernel

    runtime_kernel._STORES.clear()
    runtime_kernel._INGRESS.clear()
    return runtime_kernel


def test_status_and_deterministic_language_features(runtime):
    assert runtime.runtime_kernel_status()["active"] == "rust"
    result = runtime.preprocess_text("  Turn\u00a0off\u2014all lights?  ")
    assert result["normalized"] == "Turn off-all lights?"
    assert result["features"]["action"] == "turn"
    assert result["features"]["state"] == "off"
    assert result["features"]["quantifier"] == "all"


def test_process_resident_store_transactions_and_event_dedup(runtime, tmp_path):
    path = tmp_path / "runtime.sqlite3"
    connection = runtime.persistence_connection(path)
    with connection:
        connection.execute("CREATE TABLE values_test(id TEXT PRIMARY KEY,value TEXT)")
        connection.execute("INSERT INTO values_test VALUES(?,?)", ("a", "one"))
    with connection:
        row = connection.execute(
            "SELECT id,value FROM values_test WHERE id=?", ("a",)
        ).fetchone()
    assert row == {"id": "a", "value": "one"}
    first = runtime.append_event(
        path, "turns", "accepted", {"safe": True}, dedupe_key="one"
    )
    replay = runtime.append_event(
        path, "turns", "accepted", {"safe": False}, dedupe_key="one"
    )
    assert replay == first
    assert runtime.read_events(path, "turns")[0]["payload"] == {"safe": True}


def test_concurrent_ingress_admits_one_delivery_and_replays_response(runtime, tmp_path):
    path = tmp_path / "ingress.sqlite3"

    def admit(_):
        return runtime.admit_ingress(path, "telegram", "telegram:chat:message")

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(admit, range(32)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 31
    assert runtime.store_ingress_response(path, "telegram:chat:message", "Done.")
    assert runtime.ingress_response(path, "telegram:chat:message") == "Done."


def test_native_sqlite_uses_the_process_shared_library():
    """Keep Python and Rust on one SQLite lock manager in WAL mode."""
    if not sys.platform.startswith("linux") or shutil.which("ldd") is None:
        pytest.skip("shared-library inspection is Linux-specific")
    for module_name in (
        "_curie_runtime_kernel._curie_runtime_kernel",
        "_curie_task_engine._curie_task_engine",
    ):
        spec = importlib.util.find_spec(module_name)
        assert spec is not None and spec.origin
        dependencies = subprocess.run(
            ["ldd", spec.origin],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "libsqlite3.so" in dependencies, (
            f"{module_name} must dynamically link the process-shared SQLite library; "
            "a bundled copy can corrupt WAL databases used by Python sqlite3"
        )


def test_mixed_python_and_native_wal_writes_preserve_integrity(runtime, tmp_path):
    path = tmp_path / "mixed.sqlite3"
    with sqlite3.connect(path, timeout=10) as connection:
        connection.executescript(
            "PRAGMA journal_mode=WAL;"
            "CREATE TABLE python_writes(id INTEGER PRIMARY KEY,value TEXT);"
            "CREATE TABLE native_writes(id INTEGER PRIMARY KEY,value TEXT);"
        )

    def python_writer():
        with sqlite3.connect(path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=10000")
            for index in range(150):
                connection.execute(
                    "INSERT OR REPLACE INTO python_writes VALUES(?,?)",
                    (index, f"python-{index}"),
                )
                connection.commit()

    def native_writer():
        connection = runtime.persistence_connection(path)
        for index in range(150):
            with connection:
                connection.execute(
                    "INSERT OR REPLACE INTO native_writes VALUES(?,?)",
                    (index, f"native-{index}"),
                )

    def ingress_writer():
        for index in range(150):
            key = f"telegram:mixed:{index}"
            assert runtime.admit_ingress(path, "telegram", key, now_ms=index + 1)
            assert runtime.store_ingress_response(path, key, "ok")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(python_writer),
            pool.submit(native_writer),
            pool.submit(ingress_writer),
        ]
        for future in futures:
            future.result()

    runtime.reset_persistence(path)
    with sqlite3.connect(path, timeout=10) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert (
            connection.execute("SELECT count(*) FROM python_writes").fetchone()[0]
            == 150
        )
        assert (
            connection.execute("SELECT count(*) FROM native_writes").fetchone()[0]
            == 150
        )


def test_redaction_and_native_jsonl_pipeline(runtime, tmp_path):
    value = {
        "password": "not-for-logs",  # pragma: allowlist secret
        "output_tokens": 42,
        "message": "Authorization: Bearer abcdefghijklmnop",
    }
    redacted = runtime.redact_native(value)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["output_tokens"] == 42
    assert "abcdefghijklmnop" not in redacted["message"]
    assert runtime.record_telemetry(value)
    target = tmp_path / "events.jsonl"
    assert runtime.flush_telemetry(target, max_bytes=64_000) == 1
    persisted = json.loads(target.read_text())
    assert persisted["password"] == "[REDACTED]"


def test_bounded_document_and_archive_processing(runtime, tmp_path):
    plain = tmp_path / "note.txt"
    plain.write_text("hello\n\n\n\nworld", encoding="utf-8")
    assert (
        runtime.extract_document_native(
            str(plain),
            ".txt",
            max_source_bytes=1024,
            max_expanded_bytes=1024,
            max_chars=100,
        )
        == "hello\n\n\nworld"
    )

    document = tmp_path / "note.docx"
    with zipfile.ZipFile(document, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml", "<w:document><w:p>Hello</w:p></w:document>"
        )
    assert "Hello" in runtime.extract_document_native(
        str(document),
        ".docx",
        max_source_bytes=4096,
        max_expanded_bytes=4096,
        max_chars=100,
    )

    unsafe = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../secret", "no")
        archive.writestr("word/document.xml", "<w:p>hello</w:p>")
    with pytest.raises(ValueError, match="unsafe member"):
        runtime.extract_document_native(
            str(unsafe),
            ".docx",
            max_source_bytes=4096,
            max_expanded_bytes=4096,
            max_chars=100,
        )


def test_model_supervisor_fences_busy_residency(runtime, monkeypatch):
    monkeypatch.setenv("LLM_MAX_LOADED_MODELS", "1")
    monkeypatch.setenv("CURIE_MODEL_SUPERVISOR", "rust")
    runtime._MODEL_SUPERVISOR = None
    supervisor = runtime.model_supervisor()
    first = json.loads(supervisor.request_load("first.gguf", 100, 1))
    assert first["admitted"]
    supervisor.loaded("first.gguf", 100, 2)
    assert supervisor.acquire("first.gguf", 3)
    denied = json.loads(supervisor.request_load("second.gguf", 100, 4))
    assert denied == {
        "admitted": False,
        "evict": [],
        "reason": "all_resident_models_busy",
    }
    assert supervisor.release("first.gguf", 5)
    admitted = json.loads(supervisor.request_load("second.gguf", 100, 6))
    assert admitted["admitted"]
    assert admitted["evict"] == ["first.gguf"]
