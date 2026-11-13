"""Bounded, preview-first maintenance for Curie's own repository.

The service may observe the live checkout, run a fixed test lane in a detached
temporary worktree, and ask a coding model to repair a reproducible failure in
that worktree.  It never commits, pushes, merges, restarts, or deploys.  A
verified repair is copied into the existing immutable ``project_change``
preview/approval path before it can touch the live checkout.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from difflib import unified_diff
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

from memory import UserManager
from services.action_previews import ActionPreviewStore, preview_store, render_preview
from utils.instance_paths import assistant_state_dir

_REPORT_KIND = "bounded_autonomy_report"
_DEFAULT_TESTS = (
    "tests/test_assistant_enhancements.py",
    "tests/test_learned_skills.py",
    "tests/test_phase67_response_memory.py",
)
_SECRET_VALUE = re.compile(
    r"(?i)((?:bearer|token|password|secret|api[_-]?key)\s*[:=]?\s*)\S+"
)
_SAFE_ID = re.compile(r"^[a-f0-9]{16,32}$")
_FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".gitlab",
        ".circleci",
        ".env",
        "config",
        "credentials",
        "deploy",
        "instances",
        "models",
        "node_modules",
        "secrets",
        "vendor",
    }
)
_FORBIDDEN_FILES = frozenset(
    {
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "requirements.txt",
        "uv.lock",
        "yarn.lock",
    }
)
_GOVERNANCE_PATHS = frozenset(
    {
        "agent/action_router.py",
        "agent/tooling/policies.py",
        "services/action_previews.py",
        "services/bounded_autonomy.py",
        "services/security.py",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_output(value: str, limit: int = 12_000) -> str:
    redacted = _SECRET_VALUE.sub(r"\1[REDACTED]", str(value or ""))
    return redacted[-max(1, int(limit)) :]


def _background_capacity_available() -> bool:
    """Keep maintenance away from an already pressured interactive host."""
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.split()[0])
        if (
            values.get("MemTotal")
            and values.get("MemAvailable", 0) / values["MemTotal"] < 0.25
        ):
            return False
    except (OSError, ValueError, IndexError):
        pass
    try:
        if os.getloadavg()[0] > max(2.0, (os.cpu_count() or 2) * 0.7):
            return False
    except OSError:
        pass
    return True


def _safe_relative_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    path = Path(normalized)
    lowered = tuple(part.casefold() for part in path.parts)
    if (
        not normalized
        or normalized.startswith(".")
        or path.is_absolute()
        or ".." in path.parts
        or any(part in _FORBIDDEN_PARTS for part in lowered)
        or path.name.casefold() in _FORBIDDEN_FILES
        or normalized.casefold() in _GOVERNANCE_PATHS
    ):
        raise PermissionError(f"Autonomous maintenance cannot change {normalized!r}")
    return normalized


def _test_targets_from_env(repo_root: Path) -> tuple[str, ...]:
    configured = os.getenv("CURIE_AUTONOMY_TEST_TARGETS", "").strip()
    values = (
        tuple(item.strip() for item in configured.split(",") if item.strip())
        if configured
        else _DEFAULT_TESTS
    )
    clean = []
    for value in values[:12]:
        normalized = _safe_relative_path(value)
        if not normalized.startswith("tests/") or not normalized.endswith(".py"):
            raise ValueError("Autonomy test targets must be relative tests/*.py files")
        if (repo_root / normalized).is_file():
            clean.append(normalized)
    if not clean:
        raise RuntimeError("No configured bounded-autonomy tests exist")
    return tuple(clean)


class BoundedAutonomyService:
    """Observe, reproduce, prepare, verify, and stop at owner approval."""

    def __init__(
        self,
        repo_root: Path | None = None,
        *,
        state_root: Path | None = None,
        previews: ActionPreviewStore | None = None,
        proposer: Callable[[str, Path], dict[str, Any]] | None = None,
        python_executable: Path | None = None,
        test_targets: Sequence[str] | None = None,
    ):
        self.repo_root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
        self.state_root = (
            state_root or assistant_state_dir() / "bounded-autonomy"
        ).resolve()
        self.previews = previews or preview_store
        self._proposer = proposer
        # Preserve the venv launcher path. Resolving its symlink would execute
        # the system interpreter and silently drop Curie's installed packages.
        self.python_executable = Path(
            os.path.abspath(
                python_executable or self.repo_root / ".venv" / "bin" / "python"
            )
        )
        self._test_targets = tuple(test_targets) if test_targets else None

    def _run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: int,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            process = subprocess.run(
                list(argv),
                cwd=cwd,
                capture_output=True,
                text=False,
                shell=False,
                timeout=max(1, int(timeout)),
                env=dict(env) if env is not None else None,
                check=False,
            )
            output = _safe_output(
                (process.stdout + b"\n" + process.stderr).decode(
                    "utf-8", errors="replace"
                )
            )
            return {
                "returncode": int(process.returncode),
                "output": output,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            output = b"".join(
                part
                for part in (
                    exc.stdout if isinstance(exc.stdout, bytes) else b"",
                    exc.stderr if isinstance(exc.stderr, bytes) else b"",
                )
            ).decode("utf-8", errors="replace")
            return {
                "returncode": 124,
                "output": _safe_output(output + "\nCommand timed out."),
                "duration_seconds": round(time.perf_counter() - started, 3),
                "timed_out": True,
            }

    def _git(self, *args: str, timeout: int = 60) -> dict[str, Any]:
        return self._run(
            ("git", "-C", str(self.repo_root), *args),
            cwd=self.repo_root,
            timeout=timeout,
        )

    def _test_command(self, worktree: Path) -> tuple[str, ...]:
        targets = self._test_targets or _test_targets_from_env(self.repo_root)
        if not self.python_executable.is_file():
            raise RuntimeError("Curie's project virtual environment is unavailable")
        for target in targets:
            _safe_relative_path(str(target))
            if not (worktree / str(target)).is_file():
                raise RuntimeError(f"Configured autonomy test is missing: {target}")
        return (str(self.python_executable), "-m", "pytest", "-q", *targets)

    def _test_env(self, worktree: Path, home: Path) -> dict[str, str]:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        allowed = {"LANG", "LC_ALL", "PATH", "TERM", "TZ"}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update(
            {
                "HOME": str(home),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(worktree),
                "CURIE_LOCAL_MEMORY_DB": str(home / "memory.sqlite3"),
                "CURIE_RESOURCE_BUDGET_PATH": str(home / "budgets.json"),
                "CURIE_DIAGNOSTICS_PATH": str(home / "diagnostics.json"),
            }
        )
        return env

    def _create_worktree(self, base_sha: str) -> Path:
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        worktrees = self.state_root / "worktrees"
        worktrees.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = Path(tempfile.mkdtemp(prefix="cycle-", dir=worktrees)).resolve()
        # git requires the destination not to exist.
        path.rmdir()
        result = self._git("worktree", "add", "--detach", str(path), base_sha)
        if result["returncode"] != 0:
            shutil.rmtree(path, ignore_errors=True)
            raise RuntimeError(
                f"Could not create isolated worktree: {result['output']}"
            )
        return path

    def _remove_worktree(self, path: Path) -> None:
        try:
            self._git("worktree", "remove", "--force", str(path), timeout=60)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def _propose(self, owner_id: str, request: str, worktree: Path) -> dict[str, Any]:
        if self._proposer is not None:
            proposal = self._proposer(request, worktree)
        else:
            from agent.tooling.project_tools import _propose_project_change
            from services.resource_governor import resource_governor
            from services.self_diagnostics import diagnostic_monitor

            decision = resource_governor.admit(
                "bounded_autonomy",
                owner_id,
                6000,
                preferred_model=os.getenv("LLM_CODING_MODEL", ""),
                fallback_model=os.getenv("LLM_GENERAL_MODEL", ""),
                diagnostics=diagnostic_monitor.snapshot(owner_id=owner_id, limit=50),
            )
            if not decision.allowed:
                raise RuntimeError(
                    f"Autonomous repair paused by the resource governor: {decision.reason}"
                )
            started = time.perf_counter()

            proposal = _propose_project_change(request, worktree)
            generated_tokens = sum(
                len(str(item.get("after") or "").split())
                for item in list(proposal.get("files") or ())
            )
            resource_governor.record(
                "bounded_autonomy",
                owner_id,
                tokens=max(1, int(generated_tokens * 1.33)),
                wall_seconds=time.perf_counter() - started,
                model=decision.model or "agent-role",
            )
        files = list(proposal.get("files") or ())
        if not 1 <= len(files) <= 5:
            raise ValueError("Maintenance proposals must change one to five files")
        changed_lines = 0
        for item in files:
            relative = _safe_relative_path(str(item.get("path") or ""))
            before, after = str(item.get("before") or ""), str(item.get("after") or "")
            diff = unified_diff(before.splitlines(), after.splitlines(), lineterm="")
            changed_lines += sum(
                line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                for line in diff
            )
            item["path"] = relative
        if changed_lines > int(os.getenv("CURIE_AUTONOMY_MAX_CHANGED_LINES", "300")):
            raise ValueError("Maintenance proposal exceeds the changed-line limit")
        return proposal

    def _live_preview_payload(
        self,
        owner_id: str,
        proposal: Mapping[str, Any],
        verification: Mapping[str, Any],
    ) -> dict[str, Any]:
        live_files: list[dict[str, Any]] = []
        for item in list(proposal.get("files") or ()):
            relative = _safe_relative_path(str(item.get("path") or ""))
            target = (self.repo_root / relative).resolve()
            try:
                target.relative_to(self.repo_root)
            except ValueError as exc:
                raise PermissionError("Proposal escaped Curie's repository") from exc
            existed = target.is_file()
            before = target.read_text(encoding="utf-8") if existed else ""
            if existed != bool(item.get("existed")) or hashlib.sha256(
                before.encode("utf-8")
            ).hexdigest() != str(item.get("before_hash") or ""):
                raise RuntimeError(
                    f"{relative} differs from the isolated base; no live preview was created"
                )
            live_files.append(
                {
                    "path": relative,
                    "existed": existed,
                    "before": before,
                    "after": str(item.get("after") or ""),
                    "before_hash": hashlib.sha256(before.encode()).hexdigest(),
                }
            )
        return {
            "root": str(self.repo_root),
            "request": str(
                proposal.get("request") or "Repair a reproduced maintenance failure"
            )[:4000],
            "summary": str(
                proposal.get("summary") or "Verified bounded maintenance repair"
            )[:1000],
            "files": live_files,
            "diff": str(proposal.get("diff") or ""),
            # The repair was tested in an isolated worktree. Applying it remains
            # approval-gated; a later manual/full test run can use the normal tool.
            "run_tests": False,
            "bounded_autonomy": {
                "owner_scope_hash": hashlib.sha256(str(owner_id).encode()).hexdigest()[
                    :16
                ],
                "verification": dict(verification),
            },
        }

    def _prepare_verified_patch(
        self,
        owner_id: str,
        worktree: Path,
        failed: Mapping[str, Any],
        test_command: Sequence[str],
        env: Mapping[str, str],
    ) -> tuple[dict[str, Any] | None, str | None]:
        request = (
            "Fix only the reproduced bounded-maintenance test failure below. "
            "Preserve unrelated behavior. Do not change dependencies, deployment, credentials, "
            "approval/governance code, or the autonomy service itself.\n\n"
            + _safe_output(str(failed.get("output") or ""), 8_000)
        )
        proposal = self._propose(owner_id, request, worktree)
        from agent.tooling.project_tools import _apply_project_proposal

        _apply_project_proposal(proposal, worktree)
        verification = self._run(
            test_command,
            cwd=worktree,
            timeout=int(os.getenv("CURIE_AUTONOMY_TEST_TIMEOUT_SECONDS", "240")),
            env=env,
        )
        if verification["returncode"] != 0:
            return (
                None,
                "The candidate repair did not pass the isolated verification lane.",
            )
        payload = self._live_preview_payload(owner_id, proposal, verification)
        preview = self.previews.create(
            owner_id,
            action="project_change",
            payload=payload,
            summary=(
                f"{payload['summary']} Isolated verification passed in "
                f"{verification['duration_seconds']:.1f}s. Nothing has been applied."
            ),
            exact_changes=str(payload.get("diff") or "(No textual diff.)"),
            ttl_minutes=int(os.getenv("CURIE_AUTONOMY_PREVIEW_TTL_MINUTES", "1440")),
        )
        return preview, None

    def _save_report(self, owner_id: str, report: dict[str, Any]) -> dict[str, Any]:
        from memory.local_store import save_personal_item

        saved = save_personal_item(owner_id, _REPORT_KIND, report)
        report["id"] = saved["id"]
        return report

    def run_cycle(
        self, owner_id: str, profile: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run one non-overlapping cycle and persist its bounded evidence."""
        profile = dict(profile or UserManager.get_user_profile(owner_id) or {})
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.state_root / "cycle.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "A bounded-autonomy cycle is already running"
                ) from exc
            started = _utcnow()
            report: dict[str, Any] = {
                "id": uuid.uuid4().hex,
                "status": "error",
                "actionable": False,
                "created_at": started.isoformat(),
                "finished_at": None,
                "base_sha": "",
                "dirty_path_count": 0,
                "checks": [],
                "preview_id": "",
                "summary": "Maintenance cycle did not complete.",
            }
            worktree: Path | None = None
            test_home: Path | None = None
            try:
                head = self._git("rev-parse", "HEAD")
                if head["returncode"] != 0:
                    raise RuntimeError(
                        f"Could not read Curie's repository HEAD: {head['output']}"
                    )
                report["base_sha"] = head["output"].strip().splitlines()[-1]
                status = self._git("status", "--porcelain=v1")
                if status["returncode"] != 0:
                    raise RuntimeError(
                        f"Could not inspect Curie's repository: {status['output']}"
                    )
                dirty_paths = [
                    line for line in status["output"].splitlines() if line.strip()
                ]
                report["dirty_path_count"] = len(dirty_paths)

                diff_check = self._git("diff", "--check")
                report["checks"].append(
                    {
                        "name": "live_diff_check",
                        "passed": diff_check["returncode"] == 0,
                        "duration_seconds": diff_check["duration_seconds"],
                        "detail": (
                            "No whitespace errors in live changes."
                            if diff_check["returncode"] == 0
                            else _safe_output(diff_check["output"], 1500)
                        ),
                    }
                )

                worktree = self._create_worktree(report["base_sha"])
                test_home = self.state_root / "test-homes" / f"cycle-{uuid.uuid4().hex}"
                home = test_home
                test_command = self._test_command(worktree)
                test_result = self._run(
                    test_command,
                    cwd=worktree,
                    timeout=int(
                        os.getenv("CURIE_AUTONOMY_TEST_TIMEOUT_SECONDS", "240")
                    ),
                    env=self._test_env(worktree, home),
                )
                report["checks"].append(
                    {
                        "name": "isolated_regression_lane",
                        "passed": test_result["returncode"] == 0,
                        "duration_seconds": test_result["duration_seconds"],
                        "detail": _safe_output(test_result["output"], 2000),
                    }
                )

                if test_result["returncode"] == 0 and diff_check["returncode"] == 0:
                    report.update(
                        {
                            "status": "healthy",
                            "summary": (
                                "Bounded maintenance checks passed."
                                + (
                                    f" The live checkout has {len(dirty_paths)} existing uncommitted path(s); they were observed but not copied or changed."
                                    if dirty_paths
                                    else ""
                                )
                            ),
                        }
                    )
                elif test_result["returncode"] != 0 and profile.get(
                    "bounded_autonomy_auto_prepare", True
                ):
                    try:
                        preview, reason = self._prepare_verified_patch(
                            owner_id,
                            worktree,
                            test_result,
                            test_command,
                            self._test_env(worktree, home),
                        )
                    except Exception as exc:
                        preview, reason = (
                            None,
                            f"Patch preparation stopped safely: {type(exc).__name__}: {str(exc)[:300]}",
                        )
                    if preview:
                        report.update(
                            {
                                "status": "patch_ready",
                                "actionable": True,
                                "preview_id": str(preview["id"]),
                                "summary": "A reproducible failure was repaired and verified in an isolated worktree. The immutable patch is waiting for review.",
                            }
                        )
                    else:
                        report.update(
                            {
                                "status": "needs_attention",
                                "actionable": True,
                                "summary": reason
                                or "The isolated regression lane failed and no verified repair was produced.",
                            }
                        )
                else:
                    report.update(
                        {
                            "status": "needs_attention",
                            "actionable": True,
                            "summary": (
                                "The live diff check or isolated regression lane needs attention. Observe-only mode made no repair attempt."
                            ),
                        }
                    )
            except Exception as exc:
                report.update(
                    {
                        "status": "error",
                        "actionable": True,
                        "summary": f"Maintenance cycle stopped safely: {type(exc).__name__}: {str(exc)[:400]}",
                    }
                )
            finally:
                if worktree is not None:
                    self._remove_worktree(worktree)
                if test_home is not None:
                    shutil.rmtree(test_home, ignore_errors=True)
                report["finished_at"] = _utcnow().isoformat()
                report["duration_seconds"] = round(
                    (_utcnow() - started).total_seconds(), 3
                )

            saved = self._save_report(owner_id, report)
            updates: dict[str, Any] = {
                "bounded_autonomy_last_run_at": saved["finished_at"],
                "bounded_autonomy_last_status": saved["status"],
            }
            if saved.get("actionable"):
                updates["bounded_autonomy_pending_report_id"] = saved["id"]
            else:
                # A clean later run supersedes an undelivered older alert.
                updates["bounded_autonomy_pending_report_id"] = ""
            UserManager.update_user_profile(owner_id, updates)
            return saved

    def run_due(
        self, owner_id: str, profile: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        if not profile.get("bounded_autonomy_enabled", False):
            return None
        if not _background_capacity_available():
            return None
        pending = str(profile.get("bounded_autonomy_pending_report_id") or "")
        if pending:
            return None
        last = _parse_time(profile.get("bounded_autonomy_last_run_at"))
        interval = max(
            6.0, min(float(profile.get("bounded_autonomy_interval_hours", 24)), 168.0)
        )
        if last and _utcnow() - last < timedelta(hours=interval):
            return None
        return self.run_cycle(owner_id, profile)


def _reports(owner_id: str) -> list[dict[str, Any]]:
    from memory.local_store import list_personal_items

    return list_personal_items(owner_id, _REPORT_KIND)


def get_report(owner_id: str, report_id: str = "") -> dict[str, Any] | None:
    reports = _reports(owner_id)
    if not reports:
        return None
    if not report_id:
        return reports[-1]
    return next((item for item in reports if item.get("id") == report_id), None)


def render_digest(report: Mapping[str, Any]) -> str:
    status = str(report.get("status") or "unknown").replace("_", " ").title()
    lines = [f"**Curie maintenance: {status}**", "", str(report.get("summary") or "")]
    preview_id = str(report.get("preview_id") or "")
    if preview_id:
        lines.extend(
            [
                "",
                "Nothing has been applied, committed, pushed, restarted, or deployed.",
                f"Review the tested patch with `/autonomy review {preview_id}`.",
            ]
        )
    else:
        lines.extend(
            ["", "Nothing was changed. Use `/autonomy report` for the check evidence."]
        )
    return "\n".join(lines)


def render_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"**Bounded autonomy report — {str(report.get('status') or 'unknown').replace('_', ' ').title()}**",
        "",
        str(report.get("summary") or ""),
        "",
        f"- **Base commit:** `{str(report.get('base_sha') or '')[:12] or 'unavailable'}`",
        f"- **Existing dirty paths observed:** {int(report.get('dirty_path_count') or 0)}",
        f"- **Duration:** {float(report.get('duration_seconds') or 0):.1f}s",
    ]
    for check in list(report.get("checks") or ()):
        mark = "Passed" if check.get("passed") else "Needs attention"
        detail = str(check.get("detail") or "").strip().splitlines()[-1:]
        suffix = f" — {detail[0][:240]}" if detail else ""
        lines.append(
            f"- **{str(check.get('name') or 'check').replace('_', ' ').title()}:** {mark}{suffix}"
        )
    preview_id = str(report.get("preview_id") or "")
    if preview_id:
        lines.extend(
            [
                "",
                f"Tested preview: `{preview_id}`. Use `/autonomy review {preview_id}`.",
            ]
        )
    lines.extend(
        [
            "",
            "This system cannot commit, push, merge, restart, deploy, change credentials, or edit its own governance files.",
        ]
    )
    return "\n".join(lines)


async def handle_bounded_autonomy_command(
    owner_id: str, text: str, profile: Mapping[str, Any] | None = None
) -> str | None:
    cleaned = str(text or "").strip()
    lowered = cleaned.casefold()
    if not (lowered == "/autonomy" or lowered.startswith("/autonomy ")):
        return None
    from agent.tooling import policies

    if not policies.is_master(owner_id):
        return (
            "Bounded repository autonomy is available only to Curie's configured owner."
        )
    profile = dict(profile or UserManager.get_user_profile(owner_id) or {})
    if lowered in {"/autonomy", "/autonomy status"}:
        latest = get_report(owner_id)
        mode = (
            "prepare"
            if profile.get("bounded_autonomy_auto_prepare", True)
            else "observe"
        )
        return (
            "**Bounded autonomy**\n\n"
            f"- **State:** {'On' if profile.get('bounded_autonomy_enabled') else 'Off'}\n"
            f"- **Mode:** {mode}\n"
            f"- **Interval:** {float(profile.get('bounded_autonomy_interval_hours', 24)):.0f} hours\n"
            f"- **Last result:** {str((latest or {}).get('status') or 'Never run').replace('_', ' ')}\n"
            "- **Automatic authority:** inspect, reproduce, prepare, and test in a temporary worktree\n"
            "- **Always approval-gated:** applying a patch\n"
            "- **Never autonomous:** commit, push, merge, restart, deploy, credentials, or governance changes"
        )
    if lowered == "/autonomy on":
        UserManager.update_user_profile(
            owner_id,
            {
                "bounded_autonomy_enabled": True,
                "bounded_autonomy_auto_prepare": True,
                "bounded_autonomy_interval_hours": 24,
            },
        )
        return "Bounded autonomy is on: daily observe → reproduce → prepare → test. Applying any patch still needs your fresh approval."
    if lowered == "/autonomy off":
        UserManager.update_user_profile(owner_id, {"bounded_autonomy_enabled": False})
        return "Bounded autonomy is off. Existing reports and previews remain reviewable until they expire."
    if lowered in {"/autonomy mode observe", "/autonomy mode prepare"}:
        prepare = lowered.endswith("prepare")
        UserManager.update_user_profile(
            owner_id, {"bounded_autonomy_auto_prepare": prepare}
        )
        return (
            f"Bounded autonomy mode is now {'prepare' if prepare else 'observe-only'}."
        )
    if lowered.startswith("/autonomy interval "):
        try:
            hours = float(cleaned.rsplit(" ", 1)[-1])
        except ValueError:
            return "Use an interval from 6 to 168 hours."
        if not 6 <= hours <= 168:
            return "Use an interval from 6 to 168 hours."
        UserManager.update_user_profile(
            owner_id, {"bounded_autonomy_interval_hours": hours}
        )
        return f"Bounded maintenance will run no more often than every {hours:g} hours."
    if lowered == "/autonomy run":
        report = await asyncio.to_thread(bounded_autonomy.run_cycle, owner_id, profile)
        return render_report(report)
    if lowered == "/autonomy report":
        report = get_report(owner_id)
        return (
            render_report(report)
            if report
            else "No bounded-autonomy report exists yet."
        )
    if lowered.startswith("/autonomy report "):
        report_id = cleaned.rsplit(" ", 1)[-1]
        if not _SAFE_ID.fullmatch(report_id):
            return "That maintenance report identifier is invalid."
        report = get_report(owner_id, report_id)
        return (
            render_report(report)
            if report
            else "That owner-scoped report was not found."
        )
    if lowered.startswith("/autonomy review "):
        preview_id = cleaned.rsplit(" ", 1)[-1]
        if not re.fullmatch(r"[a-f0-9]{16}", preview_id):
            return "That maintenance preview identifier is invalid."
        preview = preview_store.load(
            owner_id, preview_id, expected_action="project_change"
        )
        from agent.action_router import execute_request
        from agent.intent_router import ToolRequest

        approval = await execute_request(
            ToolRequest(
                "project_change",
                {
                    "preview_id": preview["id"],
                    "preview_hash": preview["payload_hash"],
                },
                needs_approval=True,
                explanation="apply this isolated and tested maintenance patch",
                source="bounded_autonomy",
            ),
            owner_id,
            dict(profile),
        )
        return f"{render_preview(preview)}\n\n{approval}"
    return (
        "Use `/autonomy status`, `on`, `off`, `mode observe|prepare`, "
        "`interval 24`, `run`, `report`, or `review PREVIEW-ID`."
    )


bounded_autonomy = BoundedAutonomyService()
