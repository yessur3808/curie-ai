"""Immutable, owner-bound previews for actions that change files or remote state."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Mapping

from agent.intent_router import ToolRequest

_SPECIAL_PREVIEWABLE = frozenset(
    {"project_change", "organize_files", "draft_note", "github_contribution_publish"}
)
_PASSTHROUGH_PREVIEWABLE = frozenset(
    {
        "gmail_send",
        "calendar_create",
        "x_post",
        "x_reply",
        "x_dm_send",
        "home_control",
        "reminder_create",
        "reminder_delete",
        "browser_click",
        "browser_fill",
    }
)
_PREVIEWABLE = _SPECIAL_PREVIEWABLE | _PASSTHROUGH_PREVIEWABLE


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(payload))).hexdigest()


def _owner_hash(owner_id: str) -> str:
    return hashlib.sha256(str(owner_id).encode()).hexdigest()[:24]


class ActionPreviewStore:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(
            os.getenv(
                "CURIE_ACTION_PREVIEW_DIR",
                str(Path.home() / ".curie" / "action-previews"),
            )
        )

    def _path(self, owner_id: str, preview_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{16}", str(preview_id)):
            raise ValueError("Invalid action preview identifier")
        return self.root / _owner_hash(owner_id) / f"{preview_id}.json"

    def _cleanup_expired(self, owner_id: str) -> None:
        owner_root = self.root / _owner_hash(owner_id)
        now = datetime.now(timezone.utc)
        try:
            candidates = tuple(owner_root.glob("*.json"))
        except OSError:
            return
        for path in candidates:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                expiry = datetime.fromisoformat(
                    str(value.get("expires_at") or "").replace("Z", "+00:00")
                )
                if expiry <= now:
                    path.unlink()
            except (OSError, ValueError, TypeError):
                # Corrupt preview files are not executable and should not
                # retain potentially sensitive source material.
                try:
                    path.unlink()
                except OSError:
                    pass

    def create(
        self,
        owner_id: str,
        *,
        action: str,
        payload: Mapping[str, Any],
        summary: str,
        exact_changes: str,
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        self._cleanup_expired(owner_id)
        ttl = max(
            1, int(ttl_minutes or os.getenv("CURIE_ACTION_PREVIEW_TTL_MINUTES", "30"))
        )
        preview_id = secrets.token_hex(8)
        protected_payload = dict(payload)
        preview = {
            "schema_version": 1,
            "id": preview_id,
            "owner_scope_hash": _owner_hash(owner_id),
            "action": str(action),
            "summary": str(summary)[:1000],
            "exact_changes": str(exact_changes),
            "payload": protected_payload,
            "payload_hash": _digest(protected_payload),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=ttl)).isoformat(),
            "consumed_at": None,
        }
        path = self._path(owner_id, preview_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(preview, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        return preview

    def load(
        self,
        owner_id: str,
        preview_id: str,
        *,
        expected_hash: str = "",
        expected_action: str = "",
        allow_consumed: bool = False,
    ) -> dict[str, Any]:
        path = self._path(owner_id, preview_id)
        try:
            preview = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("That action preview no longer exists") from exc
        if preview.get("owner_scope_hash") != _owner_hash(owner_id):
            raise PermissionError("That action preview belongs to another owner")
        if expected_action and preview.get("action") != expected_action:
            raise PermissionError("The approved action does not match its preview")
        payload_hash = _digest(dict(preview.get("payload") or {}))
        if payload_hash != preview.get("payload_hash") or (
            expected_hash and expected_hash != payload_hash
        ):
            raise PermissionError("The action preview changed after it was created")
        expiry = datetime.fromisoformat(
            str(preview["expires_at"]).replace("Z", "+00:00")
        )
        if expiry <= datetime.now(timezone.utc):
            raise ValueError(
                "That action preview has expired; please generate a new one"
            )
        if preview.get("consumed_at") and not allow_consumed:
            raise ValueError("That action preview has already been used")
        return preview

    def consume(self, owner_id: str, preview_id: str, *, expected_hash: str) -> None:
        preview = self.load(
            owner_id, preview_id, expected_hash=expected_hash, allow_consumed=False
        )
        preview["consumed_at"] = datetime.now(timezone.utc).isoformat()
        preview["original_payload_hash"] = preview["payload_hash"]
        preview["payload"] = {}
        preview["payload_hash"] = _digest({})
        preview["exact_changes"] = "(Consumed preview contents removed.)"
        path = self._path(owner_id, preview_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(preview, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)

    def discard(self, owner_id: str, preview_id: str) -> None:
        try:
            self._path(owner_id, preview_id).unlink()
        except FileNotFoundError:
            pass


preview_store = ActionPreviewStore()


def render_preview(preview: Mapping[str, Any]) -> str:
    action = str(preview.get("action") or "action").replace("_", " ")
    summary = str(preview.get("summary") or "Proposed changes")
    exact = str(preview.get("exact_changes") or "(No textual changes.)")
    action_name = str(preview.get("action") or "")
    workflows = {
        "project_change": (
            "1. Revalidate every source-file hash.\n"
            "2. Apply only the displayed patch with rollback copies.\n"
            "3. Run the requested tests, if any, and return a receipt."
        ),
        "organize_files": (
            "1. Revalidate each selected file and destination.\n"
            "2. Create the approved destination folders.\n"
            "3. Perform only the displayed moves and return a receipt."
        ),
        "draft_note": (
            "1. Revalidate the note's current version.\n"
            "2. Save exactly the displayed content with an atomic write.\n"
            "3. Return the final note path."
        ),
        "github_contribution_publish": (
            "1. Revalidate the upstream commit and every source-file hash.\n"
            "2. Push the displayed patch to a Curie-named branch or fork.\n"
            "3. Open a pull request; if GitHub refuses, preserve a reviewable suggestion instead."
        ),
        "gmail_send": "1. Revalidate the recipient and message.\n2. Send exactly one email.\n3. Return Gmail's receipt.",
        "calendar_create": "1. Revalidate the event fields.\n2. Create exactly one event.\n3. Return the Google Calendar receipt and link.",
        "x_post": "1. Revalidate the post text.\n2. Publish it exactly once.\n3. Return X's post receipt.",
        "x_reply": "1. Revalidate the target post and reply.\n2. Publish the reply exactly once.\n3. Return X's receipt.",
        "x_dm_send": "1. Revalidate the recipient and message.\n2. Send the direct message exactly once.\n3. Return X's receipt.",
        "home_control": "1. Revalidate every displayed device/group target.\n2. Apply the same displayed state to the batch.\n3. Verify and report each device separately.",
        "reminder_create": "1. Revalidate the reminder text and UTC time.\n2. Save exactly one scheduled reminder.\n3. Return its identifier and due time.",
        "reminder_delete": "1. Revalidate the selected reminder scope.\n2. Remove only the displayed reminder(s).\n3. Return the deletion count.",
        "browser_click": "1. Revalidate the active page.\n2. Click only the displayed element.\n3. Return the resulting page state.",
        "browser_fill": "1. Revalidate the active page and field.\n2. Fill exactly the displayed value.\n3. Return the resulting field state.",
    }
    workflow = workflows.get(
        action_name,
        "1. Revalidate the target.\n2. Apply only the displayed change.\n3. Return a verification receipt.",
    )
    return (
        f"**Preview — {action.title()}**\n\n"
        f"{summary}\n\n"
        f"**Proposed workflow**\n\n{workflow}\n\n"
        f"**Exact changes**\n\n{exact}\n\n"
        f"Preview ID: `{preview.get('id')}`"
    )


def _organize_payload(
    params: Mapping[str, Any], owner_id: str, profile: Mapping[str, Any]
) -> tuple[dict, str, str]:
    from agent.tooling import policies

    root = policies.action_root(owner_id, dict(profile), params.get("path"))
    destination = Path(str(params.get("destination") or "organized"))
    if destination.is_absolute() or ".." in destination.parts:
        raise PermissionError("The destination must stay inside the selected project")
    destination_path = policies.validate_path(
        root / destination, root=root, write=True, allow_missing=True
    )
    files = params.get("files") or []
    if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= 50:
        raise ValueError("Select between 1 and 50 files to organize")
    moves = []
    lines = []
    for raw in files:
        relative = Path(str(raw))
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("A selected file escaped the project")
        source = policies.validate_path(root / relative, root=root)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Selected file does not exist: {relative}")
        target = policies.validate_path(
            destination_path / source.name, root=root, write=True, allow_missing=True
        )
        if target.exists() and target != source:
            raise FileExistsError(
                f"Destination already exists: {target.relative_to(root)}"
            )
        before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        moves.append(
            {
                "source": str(source.relative_to(root)),
                "destination": str(target.relative_to(root)),
                "before_hash": before_hash,
            }
        )
        lines.append(f"- `{source.relative_to(root)}` → `{target.relative_to(root)}`")
    payload = {"root": str(root), "moves": moves}
    return (
        payload,
        f"Move {len(moves)} selected file(s) into `{destination}`.",
        "\n".join(lines),
    )


def _note_payload(
    params: Mapping[str, Any], owner_id: str, profile: Mapping[str, Any]
) -> tuple[dict, str, str]:
    from agent.tooling import policies

    root = policies.action_root(owner_id, dict(profile), params.get("workspace"))
    relative = Path(str(params.get("path") or "notes/curie-note.md"))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.name.startswith(".")
    ):
        raise PermissionError("The note path must stay inside the selected project")
    target = policies.validate_path(
        root / relative, root=root, write=True, allow_missing=True
    )
    content = str(params.get("content") or "").strip()
    if not content:
        raise ValueError("The note needs some content")
    if len(content.encode("utf-8")) > 40_000:
        raise ValueError("The note is too large")
    existed = target.exists()
    before = target.read_text(encoding="utf-8") if existed else ""
    from difflib import unified_diff

    diff = (
        "\n".join(
            unified_diff(
                before.splitlines(),
                content.splitlines(),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                lineterm="",
            )
        )
        or "(No textual change.)"
    )
    payload = {
        "root": str(root),
        "path": str(relative),
        "content": content,
        "existed": existed,
        "before_hash": hashlib.sha256(before.encode()).hexdigest(),
    }
    return payload, f"Save the drafted note to `{relative}`.", f"```diff\n{diff}\n```"


def _display_value(value: Any) -> str:
    text = str(value if value is not None else "")
    fence = "```" if "```" not in text else "````"
    return f"{fence}\n{text}\n{fence}"


def _passthrough_payload(
    action: str, params: Mapping[str, Any]
) -> tuple[dict, str, str]:
    values = dict(params)
    if action == "gmail_send":
        summary = f"Send one email to `{values.get('recipient', '')}`."
        exact = (
            f"- **Recipient:** `{values.get('recipient', '')}`\n"
            f"- **Subject:** {values.get('subject', '')}\n\n"
            f"**Body**\n\n{_display_value(values.get('body', ''))}"
        )
    elif action == "calendar_create":
        summary = f"Create one calendar event: **{values.get('summary', '')}**."
        exact = "\n".join(
            f"- **{name.replace('_', ' ').title()}:** {values.get(name, '')}"
            for name in (
                "summary",
                "start",
                "end",
                "timezone",
                "location",
                "description",
            )
        )
    elif action in {"x_post", "x_reply", "x_dm_send"}:
        label = {"x_post": "post", "x_reply": "reply", "x_dm_send": "direct message"}[
            action
        ]
        summary = f"Publish one X {label}."
        metadata = []
        if values.get("post_id"):
            metadata.append(f"- **Replying to post:** `{values['post_id']}`")
        if values.get("participant_id"):
            metadata.append(f"- **Recipient user ID:** `{values['participant_id']}`")
        exact = "\n".join(
            metadata
            + ["", "**Exact text**", "", _display_value(values.get("text", ""))]
        )
    elif action == "home_control":
        targets = [str(item) for item in values.get("targets") or ()]
        if not targets and values.get("target"):
            targets = [str(values["target"])]
        summary = f"Turn {len(targets)} displayed smart-home target(s) **{values.get('state', '')}**."
        exact = "\n".join(
            [f"- **Desired state:** `{values.get('state', '')}`", "- **Targets:**"]
            + [f"  - `{target}`" for target in targets]
        )
    elif action == "reminder_create":
        summary = "Schedule one reminder."
        exact = (
            f"- **Due (UTC):** `{values.get('due_at', '')}`\n"
            f"- **Delivery channel:** `{values.get('platform', '')}`\n\n"
            f"**Reminder**\n\n{_display_value(values.get('message', ''))}"
        )
    elif action == "reminder_delete":
        scope = values.get("index")
        summary = "Delete scheduled reminder(s)."
        exact = "- **Scope:** " + (
            f"reminder #{scope}" if scope else "all pending reminders"
        )
    elif action == "browser_click":
        summary = "Click one browser element."
        exact = f"- **Element label:** `{values.get('text', '')}`"
    elif action == "browser_fill":
        summary = "Fill one browser field."
        exact = (
            f"- **Field:** `{values.get('label', '')}`\n\n"
            f"**Exact value**\n\n{_display_value(values.get('value', ''))}"
        )
    else:
        raise ValueError(f"No exact preview renderer exists for {action}")
    return {"parameters": values}, summary, exact


async def prepare_action_preview(
    req: ToolRequest, owner_id: str, profile: Mapping[str, Any]
) -> tuple[ToolRequest, str]:
    """Create the immutable preview before the approval token is issued."""
    if req.action not in _PREVIEWABLE or req.params.get("preview_id"):
        return req, ""
    if req.action == "project_change":
        from agent.tooling import policies
        from agent.tooling.project_tools import _propose_project_change

        root = policies.action_root(owner_id, dict(profile), req.params.get("path"))
        proposal = await asyncio.to_thread(
            _propose_project_change, str(req.params["request"]), root
        )
        payload = {**proposal, "run_tests": bool(req.params.get("run_tests"))}
        summary = str(proposal.get("summary") or "Apply the generated code change.")
        exact = str(proposal.get("diff") or "(No textual change.)")
    elif req.action == "organize_files":
        payload, summary, exact = await asyncio.to_thread(
            _organize_payload, req.params, owner_id, profile
        )
    elif req.action == "draft_note":
        payload, summary, exact = await asyncio.to_thread(
            _note_payload, req.params, owner_id, profile
        )
    elif req.action == "github_contribution_publish":
        # GitHub scan already prepared and persisted the exact patch.
        preview = preview_store.load(
            owner_id,
            str(req.params.get("proposal_id") or ""),
            expected_action="github_contribution_publish",
        )
        updated = replace(
            req,
            params={
                **dict(req.params),
                "preview_id": preview["id"],
                "preview_hash": preview["payload_hash"],
            },
        )
        return updated, render_preview(preview)
    else:
        payload, summary, exact = _passthrough_payload(req.action, req.params)
    preview = preview_store.create(
        owner_id,
        action=req.action,
        payload=payload,
        summary=summary,
        exact_changes=exact,
    )
    params = {
        **dict(req.params),
        "preview_id": preview["id"],
        "preview_hash": preview["payload_hash"],
    }
    updated_auth = dict(req.authorization or {})
    updated_auth.pop("parameter_hash", None)
    updated = replace(req, params=params, authorization=updated_auth)
    return updated, render_preview(preview)


def materialize_request_preview(
    owner_id: str, action: str, params: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Restore the immutable parameters after approval binding validation."""
    if action not in _PASSTHROUGH_PREVIEWABLE:
        return dict(params), None
    preview = preview_store.load(
        owner_id,
        str(params.get("preview_id") or ""),
        expected_hash=str(params.get("preview_hash") or ""),
        expected_action=action,
    )
    original = dict((preview.get("payload") or {}).get("parameters") or {})
    if not original:
        raise PermissionError(
            "The approved preview no longer contains executable parameters"
        )
    return original, preview


def consume_materialized_preview(
    owner_id: str, preview: Mapping[str, Any] | None
) -> None:
    if not preview:
        return
    preview_store.consume(
        owner_id,
        str(preview["id"]),
        expected_hash=str(preview["payload_hash"]),
    )


def discard_request_preview(owner_id: str, params: Mapping[str, Any]) -> None:
    preview_id = str(params.get("preview_id") or "")
    if preview_id:
        preview_store.discard(owner_id, preview_id)
