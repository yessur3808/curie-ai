from unittest.mock import patch
import logging

import main


def test_legacy_agent_loaders_are_retired():
    assert not hasattr(main, "load_default_agent")
    assert not hasattr(main, "load_all_agents")
    assert callable(main.load_default_workflow)
    assert callable(main.load_all_workflows)


def test_import_verifier_never_prints_token_values():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "scripts" / "test_import.py").read_text()
    assert "TELEGRAM_BOT_TOKEN', 'NOT SET')[:" not in source
    assert 'os.getenv("TELEGRAM_BOT_TOKEN")[:' not in source


def test_connector_flags_are_registered_once():
    with patch("sys.argv", ["main.py", "--telegram", "--slack"]):
        args = main.parse_args()
    assert args.telegram is True
    assert args.slack is True


def test_logging_suppresses_token_bearing_http_urls():
    main.configure_logging()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_logging_survives_unavailable_stack_dump_signal():
    with patch.object(
        main.faulthandler, "register", side_effect=OSError("unsupported")
    ):
        main.configure_logging()
