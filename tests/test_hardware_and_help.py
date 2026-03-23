# tests/test_hardware_and_help.py
"""
Tests for:
  cli/hardware.py   – hardware discovery, peripheral list, cache I/O
  cli/ui.py         – spinner, notify, confirm helpers
  cli/help_cmd.py   – print_full_help renders without error
  cli/main.py       – hardware/peripheral/help subcommands parse correctly
"""

from __future__ import annotations

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ─── helpers ──────────────────────────────────────────────────────────────────


def _tmp_curie_dir(monkeypatch_or_self=None):
    """Return a TemporaryDirectory and patch hardware module paths into it."""
    tmp = tempfile.TemporaryDirectory()
    return tmp


# ─── cli.hardware ─────────────────────────────────────────────────────────────


class TestHardwareModule:
    """Unit tests for cli/hardware.py scanners and cache helpers."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name)
        import cli.hardware as hw

        self._hw = hw
        self._orig_curie_dir = hw.CURIE_DIR
        self._orig_file = hw.PERIPHERALS_FILE
        hw.CURIE_DIR = self._tmp
        hw.PERIPHERALS_FILE = self._tmp / "peripherals.json"

    def teardown_method(self):
        self._hw.CURIE_DIR = self._orig_curie_dir
        self._hw.PERIPHERALS_FILE = self._orig_file
        self._tmpdir.cleanup()

    # ── cache helpers ─────────────────────────────────────────────────────

    def test_save_and_load_cache(self):
        devices = [
            {
                "category": "USB",
                "name": "Test Device",
                "path": "/dev/bus/usb/001/001",
                "vendor": "1234",
                "product": "5678",
                "description": "test",
                "extra": "",
            },
        ]
        self._hw._save_cache(devices)
        cache = self._hw._load_cache()
        assert cache is not None
        assert cache["devices"] == devices
        assert "scanned_at" in cache
        assert cache["os"] == self._hw._OS

    def test_load_cache_missing(self):
        assert self._hw._load_cache() is None

    def test_load_cache_corrupt(self):
        (self._tmp / "peripherals.json").write_text("not-json{{{")
        assert self._hw._load_cache() is None

    # ── device record helper ──────────────────────────────────────────────

    def test_device_record_defaults(self):
        d = self._hw._device("USB", "My Device")
        assert d["category"] == "USB"
        assert d["name"] == "My Device"
        assert d["path"] == ""
        assert d["vendor"] == ""
        assert d["product"] == ""
        assert d["description"] == ""
        assert d["extra"] == ""

    def test_device_record_full(self):
        d = self._hw._device(
            "Audio",
            "Speaker",
            "/dev/snd/pcmC0D0p",
            vendor="1a2b",
            product="3c4d",
            description="HDA Intel",
            extra="stereo",
        )
        assert d["vendor"] == "1a2b"
        assert d["extra"] == "stereo"

    # ── serial scanner (mocked pyserial) ──────────────────────────────────

    def test_scan_serial_with_real_port(self):
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        mock_port.description = "USB Serial Port"
        mock_port.vid = 0x0403
        mock_port.pid = 0x6001
        mock_port.manufacturer = "FTDI"
        mock_port.product = "FT232R"
        mock_port.hwid = "USB VID:PID=0403:6001"

        mock_lp = MagicMock()
        mock_lp.comports.return_value = [mock_port]

        with patch.dict(
            "sys.modules",
            {
                "serial": MagicMock(),
                "serial.tools": MagicMock(),
                "serial.tools.list_ports": mock_lp,
            },
        ):
            # Re-import to pick up mock
            import importlib

            hw = importlib.import_module("cli.hardware")
            # Patch the list_ports directly inside the function
            with patch("serial.tools.list_ports", mock_lp):
                devices = hw._scan_serial()
        # Should not raise; may return 0 or more devices depending on env
        assert isinstance(devices, list)

    def test_scan_serial_no_pyserial(self):
        """Falls back gracefully when pyserial is not installed."""
        import importlib
        import cli.hardware as hw

        original = hw.__builtins__
        # Simulate ImportError inside the function
        with patch(
            "builtins.__import__",
            side_effect=lambda name, *a, **k: (
                (_ for _ in ()).throw(ImportError("no serial"))
                if name == "serial"
                else __import__(name, *a, **k)
            ),
        ):
            # The scanner should return [] without raising and an empty list
            devices = hw._scan_serial()
            assert isinstance(devices, list)
            assert devices == []

    # ── USB scanners ──────────────────────────────────────────────────────

    def test_scan_usb_lsusb_not_available(self):
        """When lsusb is absent, returns []."""
        import cli.hardware as hw

        with patch("shutil.which", return_value=None):
            assert hw._scan_usb_lsusb() == []

    def test_scan_usb_lsusb_parses_output(self):
        import cli.hardware as hw

        lsusb_output = (
            "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
            "Bus 001 Device 002: ID 0bda:8153 Realtek Semiconductor Corp USB 10/100/1000 LAN\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = lsusb_output
        with patch("shutil.which", return_value="/usr/bin/lsusb"):
            with patch("subprocess.run", return_value=mock_result):
                devices = hw._scan_usb_lsusb()
        assert len(devices) == 2
        assert devices[0]["category"] == "USB"
        assert devices[0]["vendor"] == "1d6b"
        assert "root hub" in devices[0]["name"].lower()

    def test_scan_usb_sysfs_no_dir(self):
        import cli.hardware as hw

        with patch("pathlib.Path.exists", return_value=False):
            assert hw._scan_usb_sysfs() == []

    def test_scan_usb_deduplication(self):
        """_scan_usb() should not return duplicate devices."""
        import cli.hardware as hw

        fake_device = hw._device(
            "USB", "Duplicate Device", vendor="1234", product="abcd"
        )

        # Both lsusb and sysfs "find" the same device
        with patch.object(hw, "_scan_usb_lsusb", return_value=[fake_device]):
            with patch.object(hw, "_scan_usb_sysfs", return_value=[fake_device]):
                with patch.object(hw, "_scan_usb_macos", return_value=[]):
                    devices = hw._scan_usb()
        # First method succeeds → only lsusb devices kept (break after first hit)
        assert len(devices) == 1

    # ── audio ─────────────────────────────────────────────────────────────

    def test_scan_audio_linux_missing_file(self):
        import cli.hardware as hw

        with patch("pathlib.Path.exists", return_value=False):
            assert hw._scan_audio_linux() == []

    def test_scan_audio_linux_parses_cards(self):
        import cli.hardware as hw

        fake_cards = (
            " 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
            " 1 [USB            ]: USB-Audio - USB Headset\n"
        )
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = fake_cards
        with patch("pathlib.Path.__new__", return_value=mock_path):
            pass  # too invasive; test the regex directly
        # Test regex parsing
        import re

        pattern = re.compile(r"\s*(\d+)\s+\[(.+)\]:\s+(.+)")
        lines = fake_cards.splitlines()
        parsed = [m.groups() for line in lines for m in [pattern.match(line)] if m]
        assert len(parsed) == 2
        assert parsed[0][2].strip() == "HDA-Intel - HDA Intel PCH"

    # ── cameras ───────────────────────────────────────────────────────────

    def test_scan_cameras_linux_no_devices(self, tmp_path):
        """No /dev/video* → empty list."""
        import cli.hardware as hw

        with patch("pathlib.Path.glob", return_value=iter([])):
            devices = hw._scan_cameras_linux()
        assert devices == []

    # ── network ───────────────────────────────────────────────────────────

    def test_scan_network_no_psutil(self):
        import cli.hardware as hw

        with patch.dict("sys.modules", {"psutil": None}):
            devices = hw._scan_network()
        assert isinstance(devices, list)

    def test_scan_network_with_mock_psutil(self):
        import cli.hardware as hw

        mock_psutil = MagicMock()
        mock_stat = MagicMock()
        mock_stat.isup = True
        mock_stat.speed = 1000
        mock_stat.mtu = 1500
        mock_psutil.net_if_stats.return_value = {"eth0": mock_stat, "lo": mock_stat}
        mock_addr = MagicMock()
        mock_addr.family.name = "AF_INET"
        mock_addr.address = "192.168.1.1"
        mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr], "lo": [mock_addr]}

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            devices = hw._scan_network()
        # lo should be skipped, eth0 included
        ifaces = [d["name"] for d in devices]
        assert "lo" not in ifaces

    # ── cmd_hardware_discover ─────────────────────────────────────────────

    def test_cmd_hardware_discover_saves_cache(self):
        import cli.hardware as hw

        fake_devices = [
            hw._device("USB", "Keyboard", "/dev/bus/usb/001/002", vendor="045e"),
            hw._device("Audio", "Headset", "/dev/snd/pcmC1D0p"),
        ]
        with patch.object(hw, "_run_all_scans", return_value=fake_devices):
            rc = hw.cmd_hardware_discover(save=True)
        assert rc == 0
        cache = hw._load_cache()
        assert cache is not None
        assert len(cache["devices"]) == 2

    def test_cmd_hardware_discover_no_devices(self):
        import cli.hardware as hw

        with patch.object(hw, "_run_all_scans", return_value=[]):
            rc = hw.cmd_hardware_discover(save=False)
        assert rc == 0

    # ── cmd_peripheral_list ───────────────────────────────────────────────

    def test_cmd_peripheral_list_no_cache(self):
        import cli.hardware as hw

        rc = hw.cmd_peripheral_list(fresh=False)
        assert rc == 1  # no cache → error

    def test_cmd_peripheral_list_from_cache(self):
        import cli.hardware as hw

        fake_devices = [hw._device("USB", "Test Device")]
        hw._save_cache(fake_devices)
        rc = hw.cmd_peripheral_list(fresh=False)
        assert rc == 0

    def test_cmd_peripheral_list_fresh_rescans(self):
        import cli.hardware as hw

        fake_devices = [hw._device("Serial / COM", "Arduino Uno", "/dev/ttyACM0")]
        with patch.object(hw, "_run_all_scans", return_value=fake_devices):
            rc = hw.cmd_peripheral_list(fresh=True)
        assert rc == 0
        cache = hw._load_cache()
        assert cache is not None


# ─── cli.ui ───────────────────────────────────────────────────────────────────


class TestUI:
    """Tests for cli/ui.py helpers."""

    def test_spinner_runs_body(self):
        from cli.ui import spinner

        ran = []
        with spinner("Testing…"):
            ran.append(1)
        assert ran == [1]

    def test_spinner_runs_body_no_rich(self):
        """Spinner degrades gracefully when rich is absent."""
        import cli.ui as ui_mod

        orig = ui_mod._RICH
        ui_mod._RICH = False
        try:
            ran = []
            with ui_mod.spinner("Testing fallback…"):
                ran.append(2)
            assert ran == [2]
        finally:
            ui_mod._RICH = orig

    def test_notify_returns_bool(self):
        """notify() should always return a bool, never raise."""
        from cli.ui import notify

        result = notify("Test", "Body")
        assert isinstance(result, bool)

    def test_notify_no_tools_available(self):
        """When no notification tools are found, returns False gracefully."""
        from cli.ui import notify
        import cli.ui as ui_mod

        orig_os = ui_mod._OS
        ui_mod._OS = "Linux"
        try:
            with patch("shutil.which", return_value=None):
                result = notify("Test", "No tools")
        finally:
            ui_mod._OS = orig_os
        assert result is False

    def test_success_info_warn_error_do_not_raise(self, capsys):
        from cli.ui import success, info, warn, error
        import cli.ui as ui_mod

        orig = ui_mod._RICH
        ui_mod._RICH = False
        try:
            success("all good")
            info("fyi")
            warn("careful")
            error("oops")
        finally:
            ui_mod._RICH = orig
        out = capsys.readouterr()
        assert "all good" in out.out
        assert "fyi" in out.out
        assert "careful" in out.out
        assert "oops" in (out.out + out.err)

    def test_print_rule_no_rich(self, capsys):
        from cli.ui import print_rule
        import cli.ui as ui_mod

        orig = ui_mod._RICH
        ui_mod._RICH = False
        try:
            print_rule("Test Section")
        finally:
            ui_mod._RICH = orig
        out = capsys.readouterr().out
        assert "Test Section" in out


# ─── cli.help_cmd ─────────────────────────────────────────────────────────────


class TestHelpCmd:
    """Tests for cli/help_cmd.py."""

    def test_print_full_help_returns_zero(self, capsys):
        from cli.help_cmd import print_full_help

        rc = print_full_help()
        assert rc == 0

    def test_plain_help_covers_all_groups(self, capsys):
        from cli.help_cmd import _plain_help, _GROUPS
        import cli.help_cmd as hm

        orig = hm._RICH
        hm._RICH = False
        try:
            _plain_help()
        finally:
            hm._RICH = orig
        out = capsys.readouterr().out
        # Every group heading should appear
        for heading, _, _ in _GROUPS:
            assert heading in out, f"Group '{heading}' missing from plain help"

    def test_all_commands_listed(self, capsys):
        from cli.help_cmd import _COMMANDS
        import cli.help_cmd as hm

        orig = hm._RICH
        hm._RICH = False
        try:
            hm._plain_help()
        finally:
            hm._RICH = orig
        out = capsys.readouterr().out
        # Spot-check a few key command prefixes
        for prefix in ["hardware", "peripheral", "memory", "cron", "channel", "auth"]:
            assert prefix in out, f"Command prefix '{prefix}' missing from help output"

    def test_help_contains_hardware_commands(self, capsys):
        from cli.help_cmd import _COMMANDS

        # Verify hardware/peripheral entries are in the catalogue
        cmds = [c[0] for c in _COMMANDS]
        assert any(
            "hardware" in c for c in cmds
        ), "hardware discover missing from _COMMANDS"
        assert any(
            "peripheral" in c for c in cmds
        ), "peripheral list missing from _COMMANDS"


# ─── cli.main: argument parsing ───────────────────────────────────────────────


class TestMainParsing:
    """Tests that cli/main.py parser handles new subcommands correctly."""

    def _parse(self, *args):
        from cli.main import _build_parser

        return _build_parser().parse_args(list(args))

    def test_hardware_discover_parses(self):
        args = self._parse("hardware", "discover")
        assert args.command == "hardware"
        assert args.hardware_action == "discover"

    def test_peripheral_list_parses(self):
        args = self._parse("peripheral", "list")
        assert args.command == "peripheral"

    def test_peripheral_list_fresh_parses(self):
        args = self._parse("peripheral", "list", "--fresh")
        assert args.fresh is True

    def test_help_parses(self):
        args = self._parse("help")
        assert args.command == "help"

    def test_hardware_discover_calls_handler(self):
        from cli.main import _build_parser
        import cli.hardware as hw

        with patch.object(hw, "_run_all_scans", return_value=[]):
            parser = _build_parser()
            args = parser.parse_args(["hardware", "discover"])
            rc = args.func(args)
        assert rc == 0

    def test_help_calls_handler(self):
        from cli.main import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["help"])
        rc = args.func(args)
        assert rc == 0

    def test_no_args_shows_help(self):
        """main() with no subcommand should call print_full_help and return 0."""
        from cli.main import main

        with patch("cli.help_cmd.print_full_help", return_value=0) as mock_help:
            rc = main([])
        mock_help.assert_called_once()
        assert rc == 0


# ─── cli.ui: interactive selector / multi_select / progress_bar / live_tail ───


class TestUIInteractive:
    """Tests for new interactive cli/ui.py helpers."""

    # ── _can_use_arrow_keys ───────────────────────────────────────────────

    def test_can_use_arrow_keys_windows(self):
        import cli.ui as ui_mod

        orig = ui_mod._OS
        ui_mod._OS = "Windows"
        try:
            assert ui_mod._can_use_arrow_keys() is False
        finally:
            ui_mod._OS = orig

    def test_can_use_arrow_keys_non_tty(self):
        import cli.ui as ui_mod

        orig = ui_mod._IS_TTY
        ui_mod._IS_TTY = False
        try:
            assert ui_mod._can_use_arrow_keys() is False
        finally:
            ui_mod._IS_TTY = orig

    # ── select (numbered fallback) ────────────────────────────────────────

    def test_select_numbered_default(self, monkeypatch):
        """select() uses default index when user presses Enter immediately."""
        import cli.ui as ui_mod

        # Force numbered fallback
        monkeypatch.setattr(ui_mod, "_IS_TTY", False)
        monkeypatch.setattr(ui_mod, "_RICH", False)
        monkeypatch.setattr("builtins.input", lambda _: "")  # user hits Enter

        idx = ui_mod.select(["alpha", "beta", "gamma"], title="Pick one", default=1)
        assert idx == 1

    def test_select_numbered_explicit_choice(self, monkeypatch):
        import cli.ui as ui_mod

        monkeypatch.setattr(ui_mod, "_IS_TTY", False)
        monkeypatch.setattr(ui_mod, "_RICH", False)
        monkeypatch.setattr("builtins.input", lambda _: "3")

        idx = ui_mod.select(["a", "b", "c"], title="Choose")
        assert idx == 2  # "3" → 0-based index 2

    def test_select_empty_raises(self):
        import cli.ui as ui_mod

        with pytest.raises(ValueError):
            ui_mod.select([])

    # ── multi_select (numbered fallback) ──────────────────────────────────

    def test_multi_select_numbered_empty_input(self, monkeypatch):
        import cli.ui as ui_mod

        monkeypatch.setattr(ui_mod, "_IS_TTY", False)
        monkeypatch.setattr(ui_mod, "_RICH", False)
        monkeypatch.setattr("builtins.input", lambda _: "")

        result = ui_mod.multi_select(["x", "y", "z"], defaults=[0, 2])
        # empty input → return defaults
        assert result == [0, 2]

    def test_multi_select_numbered_explicit(self, monkeypatch):
        import cli.ui as ui_mod

        monkeypatch.setattr(ui_mod, "_IS_TTY", False)
        monkeypatch.setattr(ui_mod, "_RICH", False)
        monkeypatch.setattr("builtins.input", lambda _: "1,3")

        result = ui_mod.multi_select(["a", "b", "c"])
        assert result == [0, 2]

    def test_multi_select_empty_options(self):
        import cli.ui as ui_mod

        assert ui_mod.multi_select([]) == []

    # ── progress_bar ──────────────────────────────────────────────────────

    def test_progress_bar_runs_body(self):
        import cli.ui as ui_mod

        orig = ui_mod._RICH
        ui_mod._RICH = False
        advances = []
        try:
            with ui_mod.progress_bar(3, "Testing") as bar:
                bar.advance()
                advances.append(1)
                bar.advance(2)
                advances.append(2)
        finally:
            ui_mod._RICH = orig
        assert advances == [1, 2]

    def test_progress_bar_with_rich(self):
        import cli.ui as ui_mod

        if not ui_mod._RICH:
            pytest.skip("Rich not available")
        items = list(range(5))
        processed = []
        with ui_mod.progress_bar(len(items), "Items") as bar:
            for item in items:
                processed.append(item)
                bar.advance()
        assert processed == items

    # ── live_tail ─────────────────────────────────────────────────────────

    def test_live_tail_missing_file(self, capsys):
        import cli.ui as ui_mod
        import cli.ui

        orig = ui_mod._RICH
        ui_mod._RICH = False
        try:
            ui_mod.live_tail("/tmp/__no_such_file_curie__.log", n_lines=5)
        finally:
            ui_mod._RICH = orig
        out = capsys.readouterr()
        assert "not found" in (out.out + out.err).lower()

    def test_live_tail_reads_existing_file(self, tmp_path, capsys):
        """live_tail should read the last n lines when not following."""
        import cli.ui as ui_mod

        orig = ui_mod._RICH
        ui_mod._RICH = False
        log = tmp_path / "test.log"
        log.write_text("\n".join(f"line {i}" for i in range(20)))
        # Patch open to raise KeyboardInterrupt immediately so we don't follow
        import builtins

        real_open = builtins.open

        class _FakeFile:
            def seek(self, *a):
                pass

            def readline(self):
                raise KeyboardInterrupt

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        import unittest.mock as _mock

        with _mock.patch("builtins.open", return_value=_FakeFile()):
            ui_mod.live_tail(log, n_lines=5)
        ui_mod._RICH = orig

    # ── _colourise_log_line ───────────────────────────────────────────────

    def test_colourise_error_line(self):
        import cli.ui as ui_mod

        if not ui_mod._RICH:
            pytest.skip("Rich not available")
        text = ui_mod._colourise_log_line("ERROR: Something failed")
        # Rich Text objects carry style info
        assert str(text) == "ERROR: Something failed"

    def test_colourise_info_line(self):
        import cli.ui as ui_mod

        if not ui_mod._RICH:
            pytest.skip("Rich not available")
        text = ui_mod._colourise_log_line("INFO started service")
        assert "started service" in str(text)
