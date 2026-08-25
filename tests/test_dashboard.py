import json

from cli import dashboard


def test_discover_instances_from_configs_and_runtime(monkeypatch, tmp_path):
    instances = tmp_path / "instances"
    runtime = tmp_path / ".curie"
    instances.mkdir()
    runtime.mkdir()
    (instances / "andreja.env").write_text("PERSONA_FILE=andreja.json\n")
    (runtime / "instance-curie.pid").write_text("123")
    monkeypatch.setattr(dashboard, "INSTANCE_DIR", instances)
    monkeypatch.setattr(dashboard.daemon, "CURIE_DIR", runtime)

    assert dashboard.discover_instances() == ["default", "andreja", "curie"]


def test_instance_env_overlay_and_model_summary(monkeypatch, tmp_path):
    root = tmp_path
    instances = root / "instances"
    instances.mkdir()
    (root / ".env").write_text("LLM_PROVIDER_PRIORITY=llama.cpp\nLLM_GENERAL_MODEL=base.gguf\n")
    (instances / "bot.env").write_text("LLM_GENERAL_MODEL=special.gguf\n")
    monkeypatch.setattr(dashboard, "REPO_ROOT", root)
    monkeypatch.setattr(dashboard, "INSTANCE_DIR", instances)
    for key in dashboard._instance_env("bot"):
        monkeypatch.delenv(key, raising=False)

    env = dashboard._instance_env("bot")
    assert env["LLM_GENERAL_MODEL"] == "special.gguf"
    assert dashboard._model_summary(env) == "llama.cpp: special.gguf"


def test_persona_name_reads_display_name(monkeypatch, tmp_path):
    (tmp_path / "custom.json").write_text(json.dumps({"name": "Custom Helper"}))
    monkeypatch.setattr(dashboard, "PERSONA_DIR", tmp_path)
    assert dashboard._persona_name("custom.json") == "Custom Helper"


def test_dashboard_parser_defaults():
    from cli.main import _build_parser

    args = _build_parser().parse_args(["dashboard"])
    assert args.command == "dashboard"
    assert args.once is False
    assert args.interval == 1.0


def test_runtime_path_is_scoped_per_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard.daemon, "CURIE_DIR", tmp_path)
    assert dashboard._runtime_path("default", "telemetry") == tmp_path / "telemetry.json"
    assert dashboard._runtime_path("andreja", "tasks") == tmp_path / "instance-andreja-tasks.json"


def test_task_status_reports_active_work(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard.daemon, "CURIE_DIR", tmp_path)
    (tmp_path / "instance-curie-tasks.json").write_text(
        json.dumps(
            {"tasks": {"one": {"status": "running", "description": "Researching hardware", "started_at": 1}}}
        )
    )
    assert dashboard._task_status("curie") == ("Researching hardware", 1)
