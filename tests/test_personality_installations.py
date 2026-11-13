import json
from pathlib import Path
import pytest
from cli.personalities import provision
from utils.installation import bootstrap


def fixture_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "utils").mkdir()
    (source / "utils/code.py").write_text("ok = True")
    (source / ".env").write_text("TELEGRAM_BOT_TOKEN=secret")
    (source / "state").mkdir()
    (source / "state/memory.sqlite3").write_text("private history")
    persona = source / "persona.json"
    persona.write_text(
        json.dumps(
            {
                "name": "New",
                "system_prompt": "Be helpful",
                "voice": {"trained_id": "curie", "adapter": "private"},
            }
        )
    )
    return source, persona


def test_clean_installations_and_bootstrap(tmp_path, monkeypatch):
    source, persona = fixture_source(tmp_path)
    one = provision("one", persona, source, tmp_path / "bots")
    two = provision("two", persona, source, tmp_path / "bots")
    assert not (one / "state/memory.sqlite3").exists()
    assert "secret" not in (one / ".env").read_text()
    assert json.loads((one / "assets/personality/one.json").read_text())["voice"] == {
        "trained_id": "one"
    }
    assert (one / "utils/code.py").stat().st_ino != (
        two / "utils/code.py"
    ).stat().st_ino
    assert (one / ".env").stat().st_mode & 0o777 == 0o600
    assert (one / "log/inference").stat().st_mode & 0o777 == 0o700
    monkeypatch.setattr("os.environ", dict(__import__("os").environ))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "inherited")
    monkeypatch.setenv("DATABASE_URL", "inherited_database")
    bootstrap(one)
    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == ""
    assert "DATABASE_URL" not in os.environ
    assert os.environ["ASSISTANT_STATE_DIR"] == str(one / "state")
    env = one / ".env"
    env.write_text(
        env.read_text().replace(
            str(one / "state/memory.sqlite3"), str(two / "state/memory.sqlite3")
        )
    )
    with pytest.raises(RuntimeError, match="CURIE_LOCAL_MEMORY_DB"):
        bootstrap(one)


def test_validation_and_no_overwrite(tmp_path):
    source, persona = fixture_source(tmp_path)
    with pytest.raises(ValueError):
        provision("../escape", persona, source, tmp_path)
    target = provision("one", persona, source, tmp_path)
    (target / "state/keep").write_text("important")
    with pytest.raises(FileExistsError):
        provision("one", persona, source, tmp_path)
    assert (target / "state/keep").read_text() == "important"


def test_failed_copy_leaves_no_partial_installation(tmp_path, monkeypatch):
    source, persona = fixture_source(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr("cli.personalities.shutil.copy2", fail)
    with pytest.raises(OSError):
        provision("broken", persona, source, tmp_path / "bots")
    assert list((tmp_path / "bots").iterdir()) == []


def test_legacy_shared_launch_rejected():
    from cli.daemon import start_daemon

    result = start_daemon(instance="new_personality")
    assert not result["success"]
    assert "own installation" in result["message"]
