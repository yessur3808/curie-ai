"""Owner-scoped, advisory Letta specialists sharing Curie's inference worker."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import time
from dataclasses import dataclass

from utils.redaction import redact_text

SPECIALTIES = ("workflow", "engineering", "operations")
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ReviewResult:
    status: str
    text: str = ""


def configuration():
    path = Path(
        os.getenv(
            "CURIE_LETTA_SPECIALISTS_CONFIG",
            str(Path.home() / ".config/curie-letta/specialists.json"),
        )
    )
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            return {}
        return value
    except (OSError, ValueError):
        return {}


def available(specialty):
    config = configuration()
    return (
        specialty in SPECIALTIES
        and bool(config.get("enabled"))
        and bool(
            re.fullmatch(
                r"agent-local-[a-zA-Z0-9-]+",
                str(config.get("agents", {}).get(specialty, "")),
            )
        )
    )


async def run_local(argv, prompt, timeout=90):
    """Bound execution and return reason codes, never stderr or raw envelopes."""
    proc = None
    try:
        env = {
            k: v
            for k, v in os.environ.items()
            if k in {"HOME", "PATH", "LANG", "USER", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
            or k.startswith("LC_")
        }
        proc = await asyncio.create_subprocess_exec(
            *argv,
            "--tools",
            "",
            "--new",
            "--stateless",
            "--no-skills",
            "--no-mods",
            "--no-system-info-reminder",
            "--output-format",
            "json",
            "-p",
            "--",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
        # Stream with a hard cap; an unbounded child cannot fill parent memory.
        chunks, size = [], 0

        async def collect():
            nonlocal size
            while chunk := await proc.stdout.read(8192):
                size += len(chunk)
                if size > 262144:
                    raise ValueError("output_limit")
                chunks.append(chunk)
            await proc.wait()

        error_chunks = []

        async def collect_errors():
            kept = 0
            if proc.stderr is None:
                return
            while chunk := await proc.stderr.read(8192):
                if kept < 65536:
                    error_chunks.append(chunk[: 65536 - kept])
                    kept += len(error_chunks[-1])

        await asyncio.wait_for(
            asyncio.gather(collect(), collect_errors()), timeout=timeout
        )
        if proc.returncode:
            diagnostic = b"".join(error_chunks).decode(errors="replace")
            for marker in (
                "local_inference_timeout",
                "invalid_ranking_packet",
                "scout_busy",
                "local_inference_unavailable",
                "authentication_required",
            ):
                if marker in diagnostic:
                    return ReviewResult(marker)
            return ReviewResult("process_failed")
        envelope = json.loads(b"".join(chunks))
        if not isinstance(envelope, dict) or envelope.get("is_error"):
            return ReviewResult("agent_error")
        answer = envelope.get("result")
        if not isinstance(answer, str) or not answer.strip():
            return ReviewResult("invalid_output")
        if answer.lstrip().startswith("[Error"):
            return ReviewResult("inference_failed")
        return ReviewResult("completed", answer.strip())
    except asyncio.TimeoutError:
        return ReviewResult("timeout")
    except asyncio.CancelledError:
        raise
    except (ValueError, TypeError):
        return ReviewResult("invalid_output")
    except OSError:
        return ReviewResult("launch_failed")
    finally:
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
            except ProcessLookupError:
                pass


async def review(owner, specialty, text):
    from services.resource_governor import resource_governor
    from services.self_diagnostics import diagnostic_monitor
    from services.ai_scout import preferences, recent_diagnostics
    from datetime import datetime, timezone

    config = configuration()
    if not owner or owner != config.get("owner"):
        return ReviewResult("owner_not_enabled")
    if not available(specialty):
        return ReviewResult("not_configured")
    if not isinstance(text, str) or not text.strip() or len(text) > 12000:
        return ReviewResult("invalid_request")
    # Refuse private-key blocks rather than risking partial redaction.
    if re.search(r"-----BEGIN .*PRIVATE KEY-----", text):
        return ReviewResult("sensitive_input")
    prefs = preferences(owner)
    packet = {
        "specialty": specialty,
        "request_and_supplied_evidence": redact_text(text),
        "reading_focus": prefs.get("discovery_focus", "engineering"),
        "preferred_accounts": prefs.get("preferred_accounts", [])[:8],
    }
    prompt = "CURIE_SPECIALIST_PACKET_V1\n" + json.dumps(packet)
    decision = resource_governor.admit(
        "letta_specialist",
        owner,
        256,
        diagnostics=recent_diagnostics(
            diagnostic_monitor.snapshot(owner_id=owner, limit=30),
            datetime.now(timezone.utc),
        ),
    )
    if not decision.allowed or decision.max_tokens < 256:
        return ReviewResult("resource_deferred")
    lock_path = Path.home() / ".cache/curie-letta-specialists.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return ReviewResult("busy")
        started = time.monotonic()
        result = ReviewResult("cancelled")
        try:
            executable = str(Path.home() / ".local/bin/letta")
            result = await run_local(
                [
                    executable,
                    "--backend",
                    "local",
                    "--agent",
                    config["agents"][specialty],
                    "--reflection-trigger",
                    "off",
                    "--toolset",
                    "none",
                ],
                prompt,
            )
            return ReviewResult(result.status, redact_text(result.text)[:6000])
        finally:
            resource_governor.record(
                "letta_specialist",
                owner,
                tokens=256,
                wall_seconds=time.monotonic() - started,
                outcome=result.status,
            )
            fcntl.flock(lock, fcntl.LOCK_UN)
