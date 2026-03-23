# cli/metrics.py
"""
Live system-metrics dashboard powered by rich + psutil.
Shows CPU, memory, disk, network, and GPU (if available) in real time.
"""

from __future__ import annotations

import time
from typing import Optional

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.console import Console
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None

# Net-IO baseline for per-second deltas
_prev_net: Optional[object] = None
_prev_net_time: float = 0.0


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:6.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def _color_pct(pct: float) -> str:
    if pct >= 90:
        return "bold red"
    if pct >= 70:
        return "yellow"
    return "green"


def _gpu_rows() -> list[tuple[str, ...]]:
    """Return GPU info rows; empty list if no GPU / library unavailable."""
    rows: list[tuple[str, ...]] = []

    # Try pynvml (nvidia)
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            temp_struct = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            rows.append((
                f"GPU {i}: {name}",
                f"{util.gpu}%",
                f"{_fmt_bytes(mem.used)} / {_fmt_bytes(mem.total)}",
                f"{temp_struct}°C",
            ))
        pynvml.nvmlShutdown()
        return rows
    except Exception:
        pass

    # Try subprocess nvidia-smi as fallback
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    name, util_gpu, mem_used, mem_total, temp = parts[:5]
                    rows.append((
                        f"GPU: {name}",
                        f"{util_gpu}%",
                        f"{_fmt_bytes(float(mem_used) * 1024 * 1024)} / {_fmt_bytes(float(mem_total) * 1024 * 1024)}",
                        f"{temp}°C",
                    ))
            return rows
    except Exception:
        pass

    return rows


def _build_metrics_table() -> "Table":
    global _prev_net, _prev_net_time

    table = Table(
        title="⚡ Curie AI – Live System Metrics",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Component", style="bold white", min_width=22)
    table.add_column("Value / Usage", min_width=30)
    table.add_column("Details", min_width=30)

    if not PSUTIL_AVAILABLE:
        table.add_row("psutil", "[red]not installed[/red]", "pip install psutil")
        return table

    # ── CPU ──────────────────────────────────────────────────────────────
    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_count = psutil.cpu_count(logical=True)
    cpu_phys = psutil.cpu_count(logical=False)
    try:
        cpu_freq = psutil.cpu_freq()
        freq_str = f"{cpu_freq.current:.0f} MHz" if cpu_freq else "N/A"
    except Exception:
        freq_str = "N/A"
    try:
        cpu_temp_list = psutil.sensors_temperatures()
        temps = cpu_temp_list.get("coretemp") or cpu_temp_list.get("cpu_thermal") or []
        avg_temp = (sum(t.current for t in temps) / len(temps)) if temps else None
        temp_str = f"{avg_temp:.1f}°C" if avg_temp else "N/A"
    except Exception:
        temp_str = "N/A"

    per_core = psutil.cpu_percent(percpu=True)
    core_bar = " ".join(
        f"[{_color_pct(p)}]{p:4.1f}%[/]" for p in per_core[:8]
    ) + (" …" if len(per_core) > 8 else "")

    table.add_row(
        "CPU  Overall",
        Text(f"{cpu_pct:.1f}%", style=_color_pct(cpu_pct)),
        f"{cpu_phys}p/{cpu_count}t  {freq_str}  temp:{temp_str}",
    )
    table.add_row("CPU  Per-core", core_bar, "")

    # ── Memory ───────────────────────────────────────────────────────────
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    table.add_row(
        "RAM",
        Text(f"{vm.percent:.1f}%", style=_color_pct(vm.percent)),
        f"Used {_fmt_bytes(vm.used)} / {_fmt_bytes(vm.total)}  Free {_fmt_bytes(vm.available)}",
    )
    table.add_row(
        "Swap",
        Text(f"{sw.percent:.1f}%", style=_color_pct(sw.percent)),
        f"Used {_fmt_bytes(sw.used)} / {_fmt_bytes(sw.total)}",
    )

    # ── Disk ─────────────────────────────────────────────────────────────
    try:
        disk = psutil.disk_usage("/")
        table.add_row(
            "Disk  /",
            Text(f"{disk.percent:.1f}%", style=_color_pct(disk.percent)),
            f"Used {_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)}  Free {_fmt_bytes(disk.free)}",
        )
    except Exception:
        pass

    # ── Network ──────────────────────────────────────────────────────────
    try:
        now = time.time()
        net = psutil.net_io_counters()
        if _prev_net is not None and (now - _prev_net_time) > 0:
            elapsed = now - _prev_net_time
            sent_rate = (net.bytes_sent - _prev_net.bytes_sent) / elapsed
            recv_rate = (net.bytes_recv - _prev_net.bytes_recv) / elapsed
            net_detail = (
                f"↑ {_fmt_bytes(sent_rate)}/s  ↓ {_fmt_bytes(recv_rate)}/s  "
                f"total ↑{_fmt_bytes(net.bytes_sent)} ↓{_fmt_bytes(net.bytes_recv)}"
            )
        else:
            net_detail = f"total ↑{_fmt_bytes(net.bytes_sent)} ↓{_fmt_bytes(net.bytes_recv)}"
        _prev_net = net
        _prev_net_time = now
        table.add_row("Network", "I/O rates", net_detail)
    except Exception:
        pass

    # ── GPU ──────────────────────────────────────────────────────────────
    gpu_rows = _gpu_rows()
    if gpu_rows:
        for gpu_name, gpu_util, gpu_mem, gpu_temp in gpu_rows:
            try:
                gpu_pct = float(gpu_util.rstrip("%"))
            except (ValueError, AttributeError):
                gpu_pct = 0.0
            table.add_row(
                gpu_name,
                Text(gpu_util, style=_color_pct(gpu_pct)),
                f"Mem: {gpu_mem}  Temp: {gpu_temp}",
            )
    else:
        table.add_row("GPU", "—", "No GPU detected (or pynvml/nvidia-smi not available)")

    # ── Process count ────────────────────────────────────────────────────
    try:
        proc_count = len(list(psutil.process_iter()))
        table.add_row("Processes", str(proc_count), "")
    except Exception:
        pass

    return table


def show_metrics_live(refresh_rate: float = 1.0) -> None:
    """Display a live-updating metrics dashboard until Ctrl-C."""
    if not RICH_AVAILABLE:
        print("rich is not installed. Run: pip install rich")
        return
    if not PSUTIL_AVAILABLE:
        print("psutil is not installed. Run: pip install psutil")
        return

    # Prime the per-interval CPU counter
    psutil.cpu_percent(interval=None)

    with Live(console=console, refresh_per_second=1 / refresh_rate, screen=False) as live:
        try:
            while True:
                live.update(_build_metrics_table())
                time.sleep(refresh_rate)
        except KeyboardInterrupt:
            pass


def show_metrics_once() -> None:
    """Print a one-shot snapshot of metrics."""
    if not RICH_AVAILABLE or not PSUTIL_AVAILABLE:
        print("rich and psutil are required. Run: pip install rich psutil")
        return
    psutil.cpu_percent(interval=0.5)
    console.print(_build_metrics_table())
