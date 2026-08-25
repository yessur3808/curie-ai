from pathlib import Path

import pytest

from cli import daemon


def test_named_instances_have_isolated_runtime_files(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "CURIE_DIR", tmp_path)
    curie = daemon._instance_paths("curie")
    andreja = daemon._instance_paths("andreja")

    assert curie != andreja
    assert curie == (
        tmp_path / "instance-curie.pid",
        tmp_path / "instance-curie.log",
        tmp_path / "instance-curie-daemon-state.json",
    )


@pytest.mark.parametrize("name", ["../escape", "bad name", "a" * 33])
def test_unsafe_instance_names_are_rejected(name):
    with pytest.raises(ValueError):
        daemon._instance_name(name)


def test_instance_flags_are_available():
    from cli.main import _build_parser

    parser = _build_parser()
    assert parser.parse_args(["start", "--instance", "curie"]).instance == "curie"
    assert parser.parse_args(["status", "--instance", "andreja"]).instance == "andreja"
    assert parser.parse_args(["logs", "--instance", "third"]).instance == "third"


def test_startup_variant_advances_across_restarts(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "CURIE_DIR", tmp_path)
    _, _, state_file = daemon._instance_paths("andreja")
    state_file.write_text('{"startup_variant": 2}')
    assert daemon.read_daemon_state("andreja")["startup_variant"] + 1 == 3
