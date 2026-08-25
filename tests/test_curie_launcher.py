from pathlib import Path


def test_launcher_uses_python_prefix_not_executable_realpath():
    launcher = Path(__file__).resolve().parents[1] / "curie"
    source = launcher.read_text(encoding="utf-8")
    assert "sys.prefix" in source
    assert "realpath(_venv_python)" not in source


def test_installer_global_launcher_targets_management_cli():
    installer = Path(__file__).resolve().parents[1] / "install.sh"
    source = installer.read_text(encoding="utf-8")
    assert '"${INSTALL_DIR}/curie" "\\$@"' in source
    assert 'exec python main.py "\\$@"' not in source
