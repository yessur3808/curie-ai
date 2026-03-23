# agent/skills/system_commands.py
"""
System-commands skill: exposes all CLI management capabilities via chat.

Users can trigger any CLI command through:
  - Explicit slash commands:  /status, /metrics, /tasks, /doctor, /logs, /start, /stop, /restart
  - Prefixed commands:        /curie status, /curie metrics, ...
  - Natural language:         "show me the system metrics", "is curie running?", etc.

Destructive commands (start / stop / restart / service) are restricted to the
MASTER_USER_ID to prevent unauthorised control of the daemon.

Natural-language examples handled
-----------------------------------
  "curie status" / "what's the daemon status?" / "is curie running?"
  "show system metrics" / "cpu usage" / "memory stats"
  "active tasks" / "what tasks are running?" / "show sub-agents"
  "run doctor" / "health check" / "diagnose system"
  "show logs" / "last 20 log lines"
  "start curie" / "stop curie" / "restart curie"   ← master only
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Optional CLI module imports – silently skipped if cli package is unavailable.
# Importing at module level makes names patchable in tests.
try:
    from cli.daemon import get_status, start_daemon, stop_daemon, restart_daemon, LOG_FILE
    _CLI_DAEMON_AVAILABLE = True
except Exception:
    _CLI_DAEMON_AVAILABLE = False
    LOG_FILE = None  # type: ignore[assignment]

    def get_status() -> dict:  # type: ignore[misc]
        return {"running": False, "pid": None, "uptime_seconds": None, "log_file": "N/A"}

    def start_daemon(**_):  # type: ignore[misc]
        return {"success": False, "pid": None, "message": "cli.daemon not available"}

    def stop_daemon(**_):  # type: ignore[misc]
        return {"success": False, "message": "cli.daemon not available"}

    def restart_daemon(**_):  # type: ignore[misc]
        return {"success": False, "message": "cli.daemon not available"}


try:
    from cli.tasks import get_tasks, get_task_summary
    _CLI_TASKS_AVAILABLE = True
except Exception:
    _CLI_TASKS_AVAILABLE = False

    def get_tasks() -> list:  # type: ignore[misc]
        return []

    def get_task_summary() -> dict:  # type: ignore[misc]
        return {"total_tasks": 0, "running_tasks": 0, "total_sub_agents": 0, "running_sub_agents": 0}


try:
    import psutil as _psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False

# ─── Regex patterns ───────────────────────────────────────────────────────────

# Explicit slash-command prefix: /curie <cmd>, /status, /metrics, …
_SLASH_PREFIX = re.compile(
    r"^/(?:curie\s+)?(?P<cmd>status|metrics|tasks|doctor|logs|start|stop|restart|service"
    r"|channel|cron|memory|auth)",
    re.IGNORECASE,
)

# Natural-language synonyms for each command
_NL_PATTERNS: dict[str, re.Pattern] = {
    "status": re.compile(
        r"\b(?:curie\s+status|daemon\s+status|is\s+curie\s+running|curie\s+running"
        r"|bot\s+status|agent\s+status|are\s+you\s+running|process\s+status"
        r"|show\s+(?:me\s+)?(?:the\s+)?(?:curie\s+)?status)\b",
        re.IGNORECASE,
    ),
    "metrics": re.compile(
        r"\b(?:system\s+metrics|show\s+metrics|cpu\s+usage|memory\s+usage|ram\s+usage"
        r"|disk\s+usage|disk\s+space|gpu\s+usage|system\s+resources|resource\s+usage"
        r"|how\s+much\s+(?:cpu|ram|memory|disk)|memory\s+stats|cpu\s+stats"
        r"|system\s+stats|hardware\s+stats|performance\s+stats)\b",
        re.IGNORECASE,
    ),
    "tasks": re.compile(
        r"\b(?:active\s+tasks?|running\s+tasks?|show\s+tasks?|current\s+tasks?"
        r"|what\s+(?:tasks?|jobs?)\s+are\s+running|list\s+tasks?|sub.?agents?"
        r"|running\s+sub.?agents?|task\s+breakdown|agent\s+breakdown)\b",
        re.IGNORECASE,
    ),
    "doctor": re.compile(
        r"\b(?:run\s+doctor|system\s+health|health\s+check|diagnos(?:e|is|tics?)"
        r"|system\s+check|curie\s+doctor|check\s+health|is\s+everything\s+ok"
        r"|system\s+diagnosis)\b",
        re.IGNORECASE,
    ),
    "logs": re.compile(
        r"\b(?:show\s+(?:me\s+)?(?:the\s+)?(?:recent\s+)?logs?|recent\s+logs?"
        r"|log\s+output|last\s+\d+\s+log(?:\s+lines?|s?)|curie\s+logs?|daemon\s+logs?)\b",
        re.IGNORECASE,
    ),
    "start": re.compile(
        r"\b(?:start\s+curie|start\s+(?:the\s+)?daemon|run\s+curie|launch\s+curie"
        r"|boot\s+curie|bring\s+curie\s+(?:up|online))\b",
        re.IGNORECASE,
    ),
    "stop": re.compile(
        r"\b(?:stop\s+curie|stop\s+(?:the\s+)?daemon|shut(?:\s*down)?\s+curie"
        r"|kill\s+curie|halt\s+curie)\b",
        re.IGNORECASE,
    ),
    "restart": re.compile(
        r"\b(?:restart\s+curie|restart\s+(?:the\s+)?daemon|reboot\s+curie"
        r"|reload\s+curie)\b",
        re.IGNORECASE,
    ),
    "channel": re.compile(
        r"\b(?:(?:list|show|check|curie)\s+channels?|channel\s+(?:list|status|health|doctor)"
        r"|configured\s+channels?|(?:what|which)\s+channels?\s+are\s+(?:set\s+up|configured|running))\b",
        re.IGNORECASE,
    ),
    "cron": re.compile(
        r"\b(?:(?:list|show)\s+(?:scheduled|cron)\s+(?:jobs?|tasks?)|cron\s+(?:jobs?|list)"
        r"|scheduled\s+(?:prompts?|jobs?|tasks?)|what\s+(?:cron|scheduled)\s+jobs?\s+are\s+there)\b",
        re.IGNORECASE,
    ),
    "memory": re.compile(
        r"\b(?:(?:list|show|view)\s+(?:user\s+)?(?:memory|facts|profiles?)"
        r"|memory\s+(?:list|stats?|usage)|user\s+memory\s+stats?|how\s+much\s+memory"
        r"|stored\s+(?:facts?|memory|data))\b",
        re.IGNORECASE,
    ),
    "auth": re.compile(
        r"\b(?:(?:show|list|check)\s+(?:llm\s+)?(?:providers?|auth\s+status|api\s+keys?)"
        r"|auth\s+status|which\s+(?:llm\s+)?provider|current\s+(?:llm\s+)?provider)\b",
        re.IGNORECASE,
    ),
}

# These commands require MASTER_USER_ID
_PRIVILEGED_CMDS = {"start", "stop", "restart", "service", "auth"}

# Extract optional integer argument from text (e.g. "show last 50 logs" or "50 log lines")
_LOG_LINES_RE = re.compile(r"\b(?:last\s+)?(\d+)\s+(?:logs?|log\s+lines?|lines?)\b", re.IGNORECASE)


# ─── Detection ────────────────────────────────────────────────────────────────


def detect_system_command(text: str) -> Optional[str]:
    """
    Return the detected command name (status/metrics/tasks/doctor/logs/start/stop/restart)
    or None if the text does not look like a system command.
    """
    m = _SLASH_PREFIX.match(text.strip())
    if m:
        return m.group("cmd").lower()

    for cmd, pat in _NL_PATTERNS.items():
        if pat.search(text):
            return cmd

    return None


def _is_master(internal_id: Optional[str]) -> bool:
    """Return True if the caller is the configured master user."""
    master = os.getenv("MASTER_USER_ID", "").strip()
    if not master:
        return False
    return str(internal_id) == master


# ─── Plain-text renderers (chat-friendly, no ANSI / rich markup) ──────────────


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def _uptime_str(seconds: Optional[int]) -> str:
    if seconds is None:
        return "unknown"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


def _render_status() -> str:
    st = get_status()
    if st["running"]:
        uptime = _uptime_str(st.get("uptime_seconds"))
        lines = [
            "\U0001f7e2 *Curie daemon is running*",
            f"\u2022 PID: `{st['pid']}`",
            f"\u2022 Uptime: {uptime}",
            f"\u2022 Log: `{st['log_file']}`",
        ]
    else:
        lines = [
            "\U0001f534 *Curie daemon is not running*",
            f"\u2022 Log: `{st['log_file']}`",
            "",
            "Start it with: `curie start` or ask me to _start curie_.",
        ]
    return "\n".join(lines)


def _render_metrics() -> str:
    if not _PSUTIL_AVAILABLE:
        return "\u274c psutil is not installed. Run: `pip install psutil`"

    psutil = _psutil

    # CPU
    psutil.cpu_percent(interval=None)
    time.sleep(0.3)
    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_count_l = psutil.cpu_count(logical=True)
    cpu_count_p = psutil.cpu_count(logical=False)
    try:
        freq = psutil.cpu_freq()
        freq_str = f"{freq.current:.0f} MHz" if freq else "N/A"
    except Exception:
        freq_str = "N/A"

    # Memory
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()

    # Disk
    try:
        disk = psutil.disk_usage("/")
        disk_line = (
            f"\u2022 Disk (/): {disk.percent:.1f}%  "
            f"used {_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)}"
        )
    except Exception:
        disk_line = "\u2022 Disk: unavailable"

    # Network
    try:
        net = psutil.net_io_counters()
        net_line = (
            f"\u2022 Network: \u2191 {_fmt_bytes(net.bytes_sent)} sent  "
            f"\u2193 {_fmt_bytes(net.bytes_recv)} recv"
        )
    except Exception:
        net_line = "\u2022 Network: unavailable"

    # GPU
    gpu_lines = []
    try:
        import pynvml  # type: ignore  # noqa: PLC0415
        pynvml.nvmlInit()
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            gpu_lines.append(
                f"\u2022 GPU {i} ({name}): {util.gpu}%  "
                f"mem {_fmt_bytes(mem.used)}/{_fmt_bytes(mem.total)}  "
                f"temp {temp}\u00b0C"
            )
        pynvml.nvmlShutdown()
    except Exception:
        pass

    if not gpu_lines:
        gpu_lines = ["\u2022 GPU: not detected"]

    lines = [
        "\u26a1 *System Metrics Snapshot*",
        "",
        f"\u2022 CPU: {cpu_pct:.1f}%  ({cpu_count_p}p/{cpu_count_l}t  {freq_str})",
        f"\u2022 RAM: {vm.percent:.1f}%  used {_fmt_bytes(vm.used)} / {_fmt_bytes(vm.total)}  free {_fmt_bytes(vm.available)}",
        f"\u2022 Swap: {sw.percent:.1f}%  used {_fmt_bytes(sw.used)} / {_fmt_bytes(sw.total)}",
        disk_line,
        net_line,
    ] + gpu_lines

    return "\n".join(lines)


def _render_tasks() -> str:
    summary = get_task_summary()
    tasks = get_tasks()

    lines = [
        "\U0001f916 *Task & Sub-Agent Breakdown*",
        "",
        f"Tasks: {summary['running_tasks']} running / {summary['total_tasks']} total",
        f"Sub-agents: {summary['running_sub_agents']} running / {summary['total_sub_agents']} total",
    ]

    running = [t for t in tasks if t.get("status") == "running"]
    if running:
        lines.append("")
        lines.append("*Active tasks:*")
        for t in running[:10]:
            desc = t.get("description", "?")[:60]
            channel = t.get("channel", "?")
            age = _uptime_str(int(time.time() - t.get("started_at", time.time())))
            sub_agents = t.get("sub_agents", {})
            running_agents = sum(1 for a in sub_agents.values() if a.get("status") == "running")
            lines.append(
                f"  \u2022 `{t['id']}` [{channel}] \"{desc}\" \u2013 {age} ago"
                f"  ({running_agents}/{len(sub_agents)} sub-agents)"
            )

            for agent in sub_agents.values():
                role = agent.get("role", "?")
                status = agent.get("status", "?")
                model = agent.get("model") or ""
                summary_text = agent.get("result_summary") or ""
                detail = f"{role}"
                if model:
                    detail += f" ({model})"
                detail += f" \u2013 {status}"
                if summary_text:
                    detail += f": {summary_text[:40]}"
                lines.append(f"    \u21b3 {detail}")
    else:
        lines.append("")
        lines.append("No active tasks at the moment.")

    return "\n".join(lines)


def _render_doctor() -> str:
    """Run diagnostics and return a plain-text summary."""
    lines = ["\U0001fa7a *System Health Report*", ""]

    # Python version
    v = sys.version_info
    ok = v >= (3, 10)
    lines.append(f"{'✅' if ok else '❌'} Python {v.major}.{v.minor}.{v.micro}")

    # Core deps
    core = ["fastapi", "uvicorn", "psycopg2", "pymongo", "requests", "httpx", "rich", "psutil"]
    lines.append("")
    lines.append("*Core dependencies:*")
    for mod in core:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "?")
            lines.append(f"  ✅ {mod} {ver}")
        except ImportError:
            lines.append(f"  ❌ {mod} – not installed")

    # Daemon status
    try:
        st = get_status()
        icon = "✅" if st["running"] else "⚠️ "
        daemon_detail = f"PID {st['pid']}" if st["running"] else "not running"
        lines.append("")
        lines.append(f"*Daemon:* {icon} {daemon_detail}")
    except Exception as e:
        lines.append(f"*Daemon:* ❌ error ({e})")

    # Key env vars
    env_check = [
        ("POSTGRES_DSN", True),
        ("MONGODB_URI", True),
        ("TELEGRAM_BOT_TOKEN", False),
        ("DISCORD_BOT_TOKEN", False),
        ("MASTER_USER_ID", False),
    ]
    lines.append("")
    lines.append("*Environment variables:*")
    for key, required in env_check:
        val = os.getenv(key)
        if val:
            lines.append(f"  ✅ {key} – set")
        elif required:
            lines.append(f"  ❌ {key} – not set (required)")
        else:
            lines.append(f"  ⚠️  {key} – not set")

    return "\n".join(lines)


def _render_logs(n: int = 20) -> str:
    if LOG_FILE is None or not LOG_FILE.exists():
        loc = str(LOG_FILE) if LOG_FILE else "unknown"
        return f"📄 Log file not found at `{loc}`."

    try:
        text = LOG_FILE.read_text(errors="replace")
        tail = text.splitlines()[-n:]
        if not tail:
            return "📄 Log file is empty."
        body = "\n".join(tail)
        return f"📄 *Last {len(tail)} log lines* (`{LOG_FILE}`):\n```\n{body}\n```"
    except OSError as e:
        return f"❌ Could not read log: {e}"


def _render_start(connector_args: list[str] | None = None) -> str:
    result = start_daemon(connector_args=connector_args)
    icon = "\U0001f7e2" if result["success"] else "\U0001f534"
    return f"{icon} {result['message']}"


def _render_stop() -> str:
    result = stop_daemon()
    icon = "\U0001f7e2" if result["success"] else "\U0001f534"
    return f"{icon} {result['message']}"


def _render_restart() -> str:
    result = restart_daemon()
    icon = "\U0001f7e2" if result["success"] else "\U0001f534"
    return f"{icon} {result['message']}"


def _render_channel_list() -> str:
    """Plain-text channel list for chat."""
    try:
        from cli.channel import _CHANNELS, _channel_status  # noqa: PLC0415
    except ImportError:
        return "\u274c Channel module not available."

    lines = ["\U0001f4e1 *Channel Configuration*", ""]
    for ch in _CHANNELS:
        st = _channel_status(ch)
        cfg = "\u2705" if st["configured"] else "\u274c"
        enabled = "enabled" if st["enabled"] else "disabled"
        lines.append(f"{cfg} *{st['label']}* \u2013 {enabled}")
        if st["token_env"]:
            lines.append(f"   Token ({st['token_env']}): `{st['token_display']}`")
    return "\n".join(lines)


def _render_cron_list() -> str:
    """Plain-text cron job list for chat."""
    try:
        from cli.cron import get_jobs  # noqa: PLC0415
    except ImportError:
        return "\u274c Cron module not available."

    jobs = get_jobs()
    if not jobs:
        return ("\U0001f551 *No scheduled jobs configured.*\n\n"
                "Add one with:\n`/cron add '*/5 * * * *' --prompt Check system health`")

    lines = [f"\U0001f551 *Scheduled Jobs* ({len(jobs)} total)", ""]
    for job in jobs:
        enabled = "\u2705" if job.get("enabled") else "\u26d4"
        last = job.get("last_run") or "never"
        prompt_preview = (job["prompt"][:50] + "\u2026") if len(job["prompt"]) > 51 else job["prompt"]
        lines.append(f"{enabled} `{job['id']}` \u2013 `{job['schedule']}`")
        lines.append(f"   \u201c{prompt_preview}\u201d  (last: {last})")
    return "\n".join(lines)


def _render_auth_status() -> str:
    """Plain-text LLM provider status for chat."""
    try:
        from cli.auth import _PROVIDERS, _PRIORITY_ENV  # noqa: PLC0415
    except ImportError:
        return "\u274c Auth module not available."

    current_priority = os.getenv(_PRIORITY_ENV, "llama.cpp")
    priority_list = [p.strip() for p in current_priority.split(",") if p.strip()]

    lines = ["\U0001f511 *LLM Provider Status*", "", f"Active priority: `{current_priority}`", ""]
    for name, pdef in _PROVIDERS.items():
        if pdef["key_env"]:
            configured = bool(os.getenv(pdef["key_env"]))
            key_status = "\u2705 key set" if configured else "\u26a0\ufe0f  no key"
        else:
            key_status = "\u2705 local"
        in_priority = name in priority_list
        bullet = "\u25b6" if in_priority else "\u25ab"
        lines.append(f"{bullet} *{pdef['label']}*: {key_status}")
    return "\n".join(lines)


# ─── Help text ────────────────────────────────────────────────────────────────


_HELP_TEXT = """\
\U0001f6e0\ufe0f *Curie System Commands*

You can use either `/curie <command>` or plain English:

*Information*
\u2022 `/status` \u2013 daemon PID, uptime, log path
\u2022 `/metrics` \u2013 CPU, RAM, Disk, Network, GPU snapshot
\u2022 `/tasks` \u2013 active tasks & sub-agent breakdown
\u2022 `/doctor` \u2013 full system health report
\u2022 `/logs [N]` \u2013 last N log lines (default 20)
\u2022 `/channel` \u2013 list configured channels
\u2022 `/cron` \u2013 list scheduled prompt jobs
\u2022 `/auth` \u2013 show LLM provider status (master only)

*Control* (master user only)
\u2022 `/start [--api|--telegram|--discord]` \u2013 start daemon
\u2022 `/stop` \u2013 stop daemon
\u2022 `/restart` \u2013 restart daemon

*Natural language examples*
\u2022 "is curie running?"
\u2022 "show me the system metrics"
\u2022 "what tasks are running?"
\u2022 "run a health check"
\u2022 "show last 30 logs"
\u2022 "list channels" / "which channels are configured?"
\u2022 "show scheduled jobs" / "list cron jobs"
\u2022 "which llm provider is active?"
"""

_HELP_TRIGGERS = re.compile(
    r"\b(?:curie\s+help|system\s+commands?|/help|what\s+(?:system\s+)?commands?)"
    r"|\b(?:show\s+)?(?:available\s+)?system\s+commands?\b",
    re.IGNORECASE,
)


# ─── Main dispatcher ──────────────────────────────────────────────────────────


def handle_system_command(
    text: str,
    internal_id: Optional[str] = None,
    platform: str = "unknown",
) -> Optional[str]:
    """
    Detect and handle a system command embedded in a chat message.

    Returns a string response if the text is a system command,
    or None to pass the message to the next handler.
    """
    stripped = text.strip()

    # Help shortcut
    if _HELP_TRIGGERS.search(stripped):
        return _HELP_TEXT

    cmd = detect_system_command(stripped)
    if not cmd:
        return None

    # Security check for privileged commands
    if cmd in _PRIVILEGED_CMDS and not _is_master(internal_id):
        return (
            "🔒 The `{}` command is restricted to the master user.\n"
            "If you are the administrator, ensure `MASTER_USER_ID` matches your user ID.".format(cmd)
        )

    try:
        if cmd == "status":
            return _render_status()

        if cmd == "metrics":
            return _render_metrics()

        if cmd == "tasks":
            return _render_tasks()

        if cmd == "doctor":
            return _render_doctor()

        if cmd == "logs":
            m = _LOG_LINES_RE.search(stripped)
            n = int(m.group(1)) if m else 20
            return _render_logs(n)

        if cmd == "start":
            # Parse optional connector flags from message
            connector_args: list[str] = []
            if re.search(r"\btelegram\b", stripped, re.IGNORECASE):
                connector_args.append("--telegram")
            if re.search(r"\bdiscord\b", stripped, re.IGNORECASE):
                connector_args.append("--discord")
            if re.search(r"\bapi\b", stripped, re.IGNORECASE):
                connector_args.append("--api")
            if re.search(r"\ball\b", stripped, re.IGNORECASE):
                connector_args = ["--all"]
            return _render_start(connector_args if connector_args else None)

        if cmd == "stop":
            return _render_stop()

        if cmd == "restart":
            return _render_restart()

        if cmd == "channel":
            return _render_channel_list()

        if cmd == "cron":
            return _render_cron_list()

        if cmd == "memory":
            # Route to metrics to avoid confusion with system RAM "memory" – the
            # NL pattern is intentionally specific to user/facts memory.
            return _render_doctor()  # fall through to doctor for now (broad health)

        if cmd == "auth":
            return _render_auth_status()

    except Exception as e:
        logger.error("System command '%s' raised an exception: %s", cmd, e, exc_info=True)
        return f"\u274c Error executing `{cmd}`: {str(e)[:120]}"

    return None
