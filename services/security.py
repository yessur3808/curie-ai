"""Deterministic security gates, retention controls, and owner-visible status."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

DEFAULT_RETENTION = {
    "messages": 30,
    "transcripts": 7,
    "generated_speech": 0,
    "temporary_media": 0,
    "audit": 90,
    "completed_tasks": 90,
    "operational_events": 30,
}

THREAT_MODEL = {
    "connectors": ("spoofing", "replay", "oversized payload", "cross-owner access"),
    "attachments": ("malware", "archive bomb", "parser exploit", "prompt injection"),
    "retrieval": ("SSRF", "redirect abuse", "untrusted instructions"),
    "tools": ("approval bypass", "path traversal", "command injection", "replay"),
    "storage": ("secret exposure", "over-retention", "cross-owner access", "tampering"),
    "models": ("prompt injection", "false authority", "sensitive output"),
}

_EXECUTABLE_MIME = {
    "application/x-dosexec",
    "application/x-executable",
    "application/x-sharedlib",
    "application/java-archive",
}
_EXECUTABLE_SUFFIXES = {
    ".apk",
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".dmg",
    ".exe",
    ".iso",
    ".jar",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
}


def retention_policy() -> dict[str, int]:
    """Return the effective, bounded retention policy in days."""
    policy = {}
    for name, default in DEFAULT_RETENTION.items():
        key = f"CURIE_RETENTION_{name.upper()}_DAYS"
        if name == "audit":
            raw = os.getenv(key, os.getenv("CURIE_AUDIT_RETENTION_DAYS", str(default)))
        else:
            raw = os.getenv(key, str(default))
        policy[name] = max(0, min(int(raw), 3650))
    return policy


def scan_attachment(path: str, filename: str = "", content_type: str = "") -> dict:
    """Fail closed on executable, malformed, bomb-like, or malware-positive media."""
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise ValueError("Attachment must be a regular local file")
    size = source.stat().st_size
    limit = int(os.getenv("TELEGRAM_MAX_ATTACHMENT_BYTES", str(20 * 1024 * 1024)))
    if size <= 0 or size > limit:
        raise ValueError("Attachment is empty or exceeds the configured size limit")
    safe_name = Path(filename or source.name).name
    suffix = Path(safe_name).suffix.casefold()
    guessed = (content_type or mimetypes.guess_type(safe_name)[0] or "").casefold()
    if suffix in _EXECUTABLE_SUFFIXES or guessed in _EXECUTABLE_MIME:
        raise ValueError("Executable attachments are not accepted")

    archive = None
    if zipfile.is_zipfile(source):
        archive = zipfile.ZipFile(source)
        try:
            infos = archive.infolist()
            max_members = int(os.getenv("CURIE_ARCHIVE_MAX_MEMBERS", "2000"))
            expanded_limit = int(
                os.getenv("CURIE_ARCHIVE_MAX_EXPANDED_BYTES", str(100 * 1024 * 1024))
            )
            expanded = sum(max(0, item.file_size) for item in infos)
            compressed = sum(max(1, item.compress_size) for item in infos)
            if len(infos) > max_members or expanded > expanded_limit:
                raise ValueError("Archive exceeds safe expansion limits")
            if expanded > 10 * 1024 * 1024 and expanded / compressed > 100:
                raise ValueError("Archive has a suspicious compression ratio")
            for item in infos:
                member = Path(item.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("Archive contains an unsafe path")
        finally:
            archive.close()

    scanner = shutil.which("clamscan")
    required = os.getenv("CURIE_REQUIRE_MALWARE_SCANNER", "false").casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if scanner:
        completed = subprocess.run(
            [scanner, "--no-summary", "--infected", str(source)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode == 1:
            raise ValueError("Attachment was rejected by the malware scanner")
        if completed.returncode not in {0, 1}:
            raise RuntimeError("The malware scanner could not inspect this attachment")
    elif required:
        raise RuntimeError("Attachment scanning is required but ClamAV is unavailable")
    return {
        "safe": True,
        "filename": safe_name,
        "size_bytes": size,
        "malware_scanner": "clamav" if scanner else "structural_only",
        "archive_checked": zipfile.is_zipfile(source),
    }


def enforce_retention(owner_id: str | None = None) -> dict[str, int]:
    from memory.local_store import purge_expired_records

    return purge_expired_records(retention_policy(), owner_id=owner_id)


def security_status() -> dict:
    return {
        "threat_surfaces": len(THREAT_MODEL),
        "malware_scanner": "clamav" if shutil.which("clamscan") else "structural_only",
        "scanner_required": os.getenv(
            "CURIE_REQUIRE_MALWARE_SCANNER", "false"
        ).casefold()
        in {"1", "true", "yes", "on"},
        "retention_days": retention_policy(),
        "database_permissions": "0600",
        "attachments_untrusted": True,
        "approval_tokens_single_use": True,
    }


def handle_security_command(owner_id: str, text: str) -> str | None:
    command = text.strip().casefold()
    if command in {"/security", "/security status", "/privacy", "/privacy status"}:
        status = security_status()
        policy = ", ".join(
            f"{key}={value}d" for key, value in status["retention_days"].items()
        )
        return (
            f"Security: {status['malware_scanner']} attachment scanning; "
            f"{status['threat_surfaces']} documented threat surfaces; owner-scoped, "
            f"single-use approvals. Retention: {policy}."
        )
    if command == "/privacy retention":
        return "Retention policy: " + ", ".join(
            f"{key}={value} days" for key, value in retention_policy().items()
        )
    if command == "/privacy purge":
        removed = enforce_retention(str(owner_id))
        return "Retention cleanup complete: " + ", ".join(
            f"{key}={value}" for key, value in removed.items()
        )
    return None
