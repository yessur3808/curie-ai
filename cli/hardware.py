# cli/hardware.py
"""
Hardware discovery and peripheral management for Curie AI.

Commands
--------
  curie hardware discover        Scan the host for connected devices
  curie peripheral list          List previously discovered peripherals
                                 (re-scans if cache is missing or --fresh)

What is scanned
---------------
Curie enumerates devices in the following categories, using the best
available method for the current OS, with no mandatory extra dependencies:

  USB devices      lsusb (Linux/macOS), /sys/bus/usb (Linux sysfs),
                   system_profiler SPUSBDataType (macOS)
  Serial / COM     pyserial (cross-platform) – always tried first
  Audio devices    /proc/asound/cards (Linux), arecord -l (Linux),
                   system_profiler SPAudioDataType (macOS)
  Cameras / Video  /dev/video* (Linux), system_profiler SPCameraDataType (macOS)
  Bluetooth        bluetoothctl devices (Linux), system_profiler (macOS)
  Input devices    /dev/input/event* + udevadm (Linux)
  Network ifaces   psutil.net_if_stats() – cross-platform

Results are saved to ~/.curie/peripherals.json between sessions so that
``curie peripheral list`` is instant without a fresh scan.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None

# ─── Paths ────────────────────────────────────────────────────────────────────

CURIE_DIR = Path.home() / ".curie"
PERIPHERALS_FILE = CURIE_DIR / "peripherals.json"

_OS = platform.system()  # "Linux", "Darwin", "Windows"


# ─── Device record ────────────────────────────────────────────────────────────

def _device(
    category: str,
    name: str,
    path: str = "",
    vendor: str = "",
    product: str = "",
    description: str = "",
    extra: str = "",
) -> dict:
    return {
        "category": category,
        "name": name,
        "path": path,
        "vendor": vendor,
        "product": product,
        "description": description,
        "extra": extra,
    }


# ─── Serial / COM ports ───────────────────────────────────────────────────────

def _scan_serial() -> list[dict]:
    """Enumerate serial/COM ports using pyserial (cross-platform)."""
    devices: list[dict] = []
    try:
        from serial.tools import list_ports  # type: ignore
        for port in list_ports.comports():
            # Skip placeholder entries that have no real hardware
            if port.description in ("n/a", "Unknown") and port.vid is None:
                continue
            vendor = f"{port.manufacturer or ''}"
            vid_pid = f"VID:PID {port.vid:04X}:{port.pid:04X}" if port.vid and port.pid else ""
            devices.append(_device(
                category="Serial / COM",
                name=port.description or port.device,
                path=port.device,
                vendor=vendor,
                product=port.product or "",
                description=vid_pid,
                extra=port.hwid or "",
            ))
    except ImportError:
        pass
    except Exception:
        pass
    return devices


# ─── USB devices ─────────────────────────────────────────────────────────────

def _scan_usb_lsusb() -> list[dict]:
    """Parse `lsusb` output (Linux/macOS with lsusb installed)."""
    devices: list[dict] = []
    if not shutil.which("lsusb"):
        return devices
    try:
        result = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=6
        )
        if result.returncode != 0:
            return devices
        # Each line: Bus 001 Device 003: ID 1234:5678 Manufacturer Device Name
        pattern = re.compile(
            r"Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*(.*)"
        )
        for line in result.stdout.splitlines():
            m = pattern.match(line.strip())
            if m:
                bus, dev, vid, pid, desc = m.groups()
                devices.append(_device(
                    category="USB",
                    name=desc.strip() or f"USB {vid}:{pid}",
                    path=f"/dev/bus/usb/{bus.zfill(3)}/{dev.zfill(3)}",
                    vendor=vid,
                    product=pid,
                    description=f"Bus {bus} Dev {dev}",
                ))
    except Exception:
        pass
    return devices


def _scan_usb_sysfs() -> list[dict]:
    """Scan /sys/bus/usb/devices on Linux for connected USB devices."""
    devices: list[dict] = []
    usb_root = Path("/sys/bus/usb/devices")
    if not usb_root.exists():
        return devices

    def _read(p: Path) -> str:
        try:
            return p.read_text().strip()
        except OSError:
            return ""

    for entry in sorted(usb_root.iterdir()):
        # Only real device nodes (e.g. "1-1", "1-1.2") – skip usb1/usb2 hubs
        name = entry.name
        if not re.match(r"^\d+-\d+", name):
            continue
        vendor = _read(entry / "idVendor")
        product = _read(entry / "idProduct")
        manufacturer = _read(entry / "manufacturer")
        prod_name = _read(entry / "product")
        if not vendor:
            continue
        devices.append(_device(
            category="USB",
            name=prod_name or f"USB {vendor}:{product}",
            path=str(entry),
            vendor=vendor,
            product=product,
            description=manufacturer,
        ))
    return devices


def _scan_usb_macos() -> list[dict]:
    """Use system_profiler on macOS to enumerate USB devices."""
    devices: list[dict] = []
    if _OS != "Darwin" or not shutil.which("system_profiler"):
        return devices
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return devices
        data = json.loads(result.stdout)
        for section in data.get("SPUSBDataType", []):
            for item in section.get("_items", []):
                devices.append(_device(
                    category="USB",
                    name=item.get("_name", "Unknown USB"),
                    vendor=item.get("vendor_id", ""),
                    product=item.get("product_id", ""),
                    description=item.get("manufacturer", ""),
                ))
    except Exception:
        pass
    return devices


def _scan_usb() -> list[dict]:
    """Try all USB enumeration methods, deduplicate by name."""
    seen: set[str] = set()
    results: list[dict] = []
    for fn in [_scan_usb_lsusb, _scan_usb_sysfs, _scan_usb_macos]:
        for d in fn():
            key = d["name"].strip().lower()
            if key and key not in seen:
                seen.add(key)
                results.append(d)
        if results:
            break  # use the first method that works
    return results


# ─── Audio devices ────────────────────────────────────────────────────────────

def _scan_audio_linux() -> list[dict]:
    """Read /proc/asound/cards and arecord -l on Linux."""
    devices: list[dict] = []
    cards_file = Path("/proc/asound/cards")
    if cards_file.exists():
        for line in cards_file.read_text().splitlines():
            # " 0 [PCH            ]: HDA-Intel - HDA Intel PCH"
            m = re.match(r"\s*(\d+)\s+\[(.+)\]:\s+(.+)", line)
            if m:
                idx, short, full = m.groups()
                devices.append(_device(
                    category="Audio",
                    name=full.strip(),
                    path=f"/dev/snd/controlC{idx}",
                    description=f"Card {idx} ({short.strip()})",
                ))
    return devices


def _scan_audio_macos() -> list[dict]:
    """Use system_profiler on macOS."""
    devices: list[dict] = []
    if _OS != "Darwin" or not shutil.which("system_profiler"):
        return devices
    try:
        result = subprocess.run(
            ["system_profiler", "SPAudioDataType", "-json"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            return devices
        data = json.loads(result.stdout)
        for section in data.get("SPAudioDataType", []):
            for item in section.get("_items", []):
                devices.append(_device(
                    category="Audio",
                    name=item.get("_name", "Audio Device"),
                    description=item.get("coreaudio_device_transport", ""),
                ))
    except Exception:
        pass
    return devices


def _scan_audio() -> list[dict]:
    if _OS == "Linux":
        return _scan_audio_linux()
    if _OS == "Darwin":
        return _scan_audio_macos()
    return []


# ─── Cameras / Video ─────────────────────────────────────────────────────────

def _scan_cameras_linux() -> list[dict]:
    """List /dev/video* devices on Linux."""
    devices: list[dict] = []
    for v in sorted(Path("/dev").glob("video*")):
        name = v.name
        # Try to read a human-readable name via v4l2 or udevadm
        label = name
        try:
            result = subprocess.run(
                ["udevadm", "info", "--query=property", f"--name={v}"],
                capture_output=True, text=True, timeout=3,
            )
            for prop_line in result.stdout.splitlines():
                if prop_line.startswith("ID_V4L_PRODUCT="):
                    label = prop_line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
        devices.append(_device(
            category="Camera / Video",
            name=label,
            path=str(v),
        ))
    return devices


def _scan_cameras_macos() -> list[dict]:
    devices: list[dict] = []
    if not shutil.which("system_profiler"):
        return devices
    try:
        result = subprocess.run(
            ["system_profiler", "SPCameraDataType", "-json"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            return devices
        data = json.loads(result.stdout)
        for section in data.get("SPCameraDataType", []):
            for item in section.get("_items", []):
                devices.append(_device(
                    category="Camera / Video",
                    name=item.get("_name", "Camera"),
                    description=item.get("spcamera_unique-id", ""),
                ))
    except Exception:
        pass
    return devices


def _scan_cameras() -> list[dict]:
    if _OS == "Linux":
        return _scan_cameras_linux()
    if _OS == "Darwin":
        return _scan_cameras_macos()
    return []


# ─── Bluetooth ────────────────────────────────────────────────────────────────

def _scan_bluetooth_linux() -> list[dict]:
    devices: list[dict] = []
    if not shutil.which("bluetoothctl"):
        return devices
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, text=True, timeout=4,
        )
        for line in result.stdout.splitlines():
            # "Device AA:BB:CC:DD:EE:FF My Device"
            m = re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line.strip(), re.IGNORECASE)
            if m:
                addr, name = m.groups()
                devices.append(_device(
                    category="Bluetooth",
                    name=name.strip() or addr,
                    path=addr,
                    description="paired/known device",
                ))
    except Exception:
        pass
    return devices


def _scan_bluetooth_macos() -> list[dict]:
    devices: list[dict] = []
    if not shutil.which("system_profiler"):
        return devices
    try:
        result = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            return devices
        data = json.loads(result.stdout)
        for section in data.get("SPBluetoothDataType", []):
            for cat in ["device_connected", "device_not_connected"]:
                for item in section.get(cat, []):
                    for dev_name, dev_info in item.items():
                        devices.append(_device(
                            category="Bluetooth",
                            name=dev_name,
                            description=dev_info.get("device_minorType", ""),
                            extra=dev_info.get("device_address", ""),
                        ))
    except Exception:
        pass
    return devices


def _scan_bluetooth() -> list[dict]:
    if _OS == "Linux":
        return _scan_bluetooth_linux()
    if _OS == "Darwin":
        return _scan_bluetooth_macos()
    return []


# ─── Input devices ────────────────────────────────────────────────────────────

def _scan_input_linux() -> list[dict]:
    """List /dev/input/event* devices on Linux with udevadm labels."""
    devices: list[dict] = []
    for ev in sorted(Path("/dev/input").glob("event*")) if Path("/dev/input").exists() else []:
        label = ev.name
        dev_type = ""
        try:
            result = subprocess.run(
                ["udevadm", "info", "--query=property", f"--name={ev}"],
                capture_output=True, text=True, timeout=3,
            )
            for prop_line in result.stdout.splitlines():
                if prop_line.startswith("NAME="):
                    label = prop_line.split("=", 1)[1].strip().strip('"')
                elif prop_line.startswith("ID_INPUT_"):
                    kind = prop_line.split("=")[0].replace("ID_INPUT_", "").lower()
                    dev_type = kind
        except Exception:
            pass
        devices.append(_device(
            category="Input",
            name=label,
            path=str(ev),
            description=dev_type,
        ))
    return devices


def _scan_input() -> list[dict]:
    if _OS == "Linux":
        return _scan_input_linux()
    return []


# ─── Network interfaces ───────────────────────────────────────────────────────

def _scan_network() -> list[dict]:
    """List network interfaces using psutil (cross-platform)."""
    devices: list[dict] = []
    try:
        import psutil  # type: ignore
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        for iface, stat in stats.items():
            if iface == "lo":
                continue  # skip loopback
            addr_list = addrs.get(iface, [])
            ipv4 = next((a.address for a in addr_list if a.family.name == "AF_INET"), "")
            devices.append(_device(
                category="Network",
                name=iface,
                description=f"{'up' if stat.isup else 'down'}  speed:{stat.speed}Mbps  mtu:{stat.mtu}",
                extra=ipv4,
            ))
    except ImportError:
        pass
    except Exception:
        pass
    return devices


# ─── Main scan ────────────────────────────────────────────────────────────────

def _run_all_scans() -> list[dict]:
    """
    Run all device scanners and return a combined list.
    Heavy work is done here; callers should wrap with a spinner.
    """
    all_devices: list[dict] = []
    all_devices.extend(_scan_serial())
    all_devices.extend(_scan_usb())
    all_devices.extend(_scan_audio())
    all_devices.extend(_scan_cameras())
    all_devices.extend(_scan_bluetooth())
    all_devices.extend(_scan_input())
    all_devices.extend(_scan_network())
    return all_devices


def _save_cache(devices: list[dict]) -> None:
    CURIE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "os": _OS,
        "devices": devices,
    }
    PERIPHERALS_FILE.write_text(json.dumps(payload, indent=2))


def _load_cache() -> Optional[dict]:
    if not PERIPHERALS_FILE.exists():
        return None
    try:
        return json.loads(PERIPHERALS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ─── Rendering ────────────────────────────────────────────────────────────────

def _render_devices(devices: list[dict], scanned_at: str = "") -> None:
    """Print devices grouped by category using Rich table or plain text."""
    if not devices:
        if _RICH:
            _console.print("[yellow]No peripherals found.[/yellow]")
        else:
            print("No peripherals found.")
        return

    # Group by category
    groups: dict[str, list[dict]] = {}
    for d in devices:
        groups.setdefault(d["category"], []).append(d)

    # Category icons
    cat_icons = {
        "USB": "🔌",
        "Serial / COM": "📡",
        "Audio": "🔊",
        "Camera / Video": "📷",
        "Bluetooth": "📶",
        "Input": "⌨️ ",
        "Network": "🌐",
    }

    if _RICH:
        if scanned_at:
            _console.print(f"\n[dim]Last scanned: {scanned_at[:19]} UTC[/dim]")

        for cat, items in groups.items():
            icon = cat_icons.get(cat, "•")
            table = Table(
                title=f"{icon} {cat}  ({len(items)} device{'s' if len(items) != 1 else ''})",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold cyan",
                title_style="bold white",
                expand=False,
            )
            table.add_column("Name", min_width=28)
            table.add_column("Path / Address", min_width=20)
            table.add_column("Vendor / ID", min_width=14)
            table.add_column("Detail", min_width=24)

            for d in items:
                table.add_row(
                    d["name"] or "—",
                    d["path"] or "—",
                    f"{d['vendor']}:{d['product']}" if d["vendor"] and d["product"] else (d["vendor"] or "—"),
                    d["description"] or d["extra"] or "—",
                )
            _console.print(table)
    else:
        if scanned_at:
            print(f"\nLast scanned: {scanned_at[:19]} UTC")
        for cat, items in groups.items():
            icon = cat_icons.get(cat, "•")
            print(f"\n{icon} {cat} ({len(items)})")
            print("  " + "-" * 60)
            for d in items:
                row = f"  {d['name']:<30} {d['path']:<22} {d['description'] or d['extra'] or ''}"
                print(row)


# ─── Public commands ──────────────────────────────────────────────────────────

def cmd_hardware_discover(save: bool = True) -> int:
    """
    Scan the host for connected hardware peripherals.

    Shows an animated spinner during the scan, then displays a grouped
    table of every device found.  Results are cached to
    ~/.curie/peripherals.json for use by ``curie peripheral list``.
    """
    from cli.ui import spinner, success, info  # noqa: PLC0415

    if _RICH:
        _console.print(
            "\n[bold cyan]🔍 Curie Hardware Discovery[/bold cyan]\n"
            f"[dim]Scanning connected devices on {_OS}…[/dim]\n"
        )

    devices: list[dict] = []

    with spinner("Scanning USB, serial, audio, cameras, Bluetooth, input…",
                 done_label=None):
        devices = _run_all_scans()

    total = len(devices)
    if _RICH:
        _console.print(f"[green]✅ Found [bold]{total}[/bold] device{'s' if total != 1 else ''}[/green]\n")
    else:
        print(f"Found {total} devices.")

    _render_devices(devices)

    if save and devices:
        _save_cache(devices)
        cache_path = PERIPHERALS_FILE
        if _RICH:
            _console.print(f"\n[dim]Results cached → {cache_path}[/dim]")
        else:
            print(f"\nCached → {cache_path}")

    return 0


def cmd_peripheral_list(fresh: bool = False) -> int:
    """
    List connected peripherals.

    Uses the cached scan from ~/.curie/peripherals.json if available.
    Pass ``fresh=True`` (or ``--fresh`` on the CLI) to re-scan first.
    """
    if fresh:
        return cmd_hardware_discover(save=True)

    cache = _load_cache()
    if cache is None:
        if _RICH:
            _console.print(
                "[yellow]No peripheral cache found.[/yellow]\n"
                "Run [bold]curie hardware discover[/bold] to scan for devices, "
                "or use [bold]curie peripheral list --fresh[/bold]."
            )
        else:
            print("No peripheral cache. Run: curie hardware discover")
        return 1

    devices = cache.get("devices", [])
    scanned_at = cache.get("scanned_at", "")

    if _RICH:
        _console.print(
            "\n[bold cyan]📋 Curie – Connected Peripherals[/bold cyan]\n"
            f"[dim]Cache from: {scanned_at[:19]} UTC  •  OS: {cache.get('os', '?')}[/dim]\n"
        )
    else:
        print("Connected Peripherals (cached)")

    _render_devices(devices, scanned_at=scanned_at)

    if _RICH:
        _console.print(
            "\n[dim]Tip: run [bold]curie hardware discover[/bold] to refresh, "
            "or [bold]curie peripheral list --fresh[/bold][/dim]"
        )
    return 0
