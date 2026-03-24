# agent/tools/canvas.py
"""
Live Canvas tool — agent-driven visual workspace.

The canvas is a shared, persistent whiteboard where Curie AI and its
sub-agents can place, update, and remove named *nodes*.  Each node is a
small data record with a type, title, content, and position on the
2-D canvas.

State is persisted to ``~/.curie/canvas.json`` so it survives restarts
and is immediately visible in the ``curie tasks --web`` browser dashboard
(served under the ``/canvas`` route).

Node schema
-----------
{
    "id":       str,   # unique slug
    "type":     str,   # "text" | "code" | "image" | "url" | "result"
    "title":    str,
    "content":  str,
    "x":        int,   # grid column (0-based)
    "y":        int,   # grid row    (0-based)
    "color":    str,   # CSS color hint (optional)
    "created_at": float,   # Unix timestamp
    "updated_at": float,
    "author":   str,   # agent or user ID
}

Natural-language triggers
--------------------------
  "add to canvas: Hello world"
  "canvas add note: meeting notes"
  "put a code node on the canvas with …"
  "show canvas"
  "list canvas nodes"
  "clear canvas"
  "remove canvas node my-note"
  "update canvas node my-note: new text"
"""

from __future__ import annotations

import json
import logging
import re
import time
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "canvas"

_CURIE_DIR = Path.home() / ".curie"
_CANVAS_FILE = _CURIE_DIR / "canvas.json"
_lock = threading.Lock()

# ── Intent patterns ──────────────────────────────────────────────────────────

_CANVAS_KEYWORDS = re.compile(
    r"\bcanvas\b|\badd (a |an )?(note|node|text|code|result|block)\b",
    re.IGNORECASE,
)


def is_tool_query(message: str) -> bool:
    return bool(_CANVAS_KEYWORDS.search(message))


# ── Persistence helpers ──────────────────────────────────────────────────────


def _load() -> dict:
    try:
        return json.loads(_CANVAS_FILE.read_text())
    except Exception:
        return {"nodes": {}}


def _save(data: dict) -> None:
    _CURIE_DIR.mkdir(parents=True, exist_ok=True)
    _CANVAS_FILE.write_text(json.dumps(data, indent=2))


# ── Public CRUD ───────────────────────────────────────────────────────────────


def get_nodes() -> list[dict]:
    """Return all canvas nodes sorted by position (y then x)."""
    data = _load()
    nodes = list(data.get("nodes", {}).values())
    nodes.sort(key=lambda n: (n.get("y", 0), n.get("x", 0)))
    return nodes


def add_node(
    title: str,
    content: str,
    node_type: str = "text",
    *,
    node_id: Optional[str] = None,
    x: int = 0,
    y: int = 0,
    color: str = "",
    author: str = "agent",
) -> dict:
    """Add or replace a canvas node. Returns the new node dict."""
    with _lock:
        data = _load()
        nodes = data.setdefault("nodes", {})
        # Auto-position: place after last existing node in the same column
        if x == 0 and y == 0 and nodes:
            max_y = max(n.get("y", 0) for n in nodes.values())
            y = max_y + 1
        nid = node_id or re.sub(r"[^a-z0-9_-]", "-", title.lower())[:32] or str(uuid.uuid4())[:8]
        now = time.time()
        node: dict[str, Any] = {
            "id": nid,
            "type": node_type,
            "title": title,
            "content": content,
            "x": x,
            "y": y,
            "color": color,
            "created_at": nodes[nid]["created_at"] if nid in nodes else now,
            "updated_at": now,
            "author": author,
        }
        nodes[nid] = node
        _save(data)
        return node


def remove_node(node_id: str) -> bool:
    """Remove a node by ID. Returns True if it existed."""
    with _lock:
        data = _load()
        nodes = data.get("nodes", {})
        if node_id not in nodes:
            return False
        del nodes[node_id]
        _save(data)
        return True


def clear_canvas() -> int:
    """Remove all nodes. Returns the count removed."""
    with _lock:
        data = _load()
        count = len(data.get("nodes", {}))
        data["nodes"] = {}
        _save(data)
        return count


# ── Intent parsing helpers ────────────────────────────────────────────────────

_TYPE_MAP = {
    "code": "code",
    "script": "code",
    "snippet": "code",
    "result": "result",
    "output": "result",
    "url": "url",
    "link": "url",
    "image": "image",
    "picture": "image",
    "photo": "image",
}

_ADD_RE = re.compile(
    r"canvas\s+add\s+(?P<type>\w+)\s*[:\-–]\s*(?P<content>.+)|"
    r"add\s+(?:a\s+|an\s+)?(?P<type2>note|node|text|code|result|block)\s+(?:to\s+(?:the\s+)?canvas\s*)?[:\-–]?\s*(?P<content2>.+)|"
    r"(?:put|place|write|post)\s+(?P<content3>.+?)\s+on\s+(?:the\s+)?canvas",
    re.IGNORECASE | re.DOTALL,
)

_REMOVE_RE = re.compile(
    r"(?:remove|delete|drop)\s+(?:canvas\s+)?node\s+[\"']?(?P<id>[a-z0-9_\- ]+)[\"']?",
    re.IGNORECASE,
)

_UPDATE_RE = re.compile(
    r"(?:update|edit|change)\s+(?:canvas\s+)?node\s+[\"']?(?P<id>[a-z0-9_\- ]+)[\"']?\s*[:\-–]\s*(?P<content>.+)",
    re.IGNORECASE | re.DOTALL,
)


# ── Main handler ──────────────────────────────────────────────────────────────


async def handle_tool_query(
    message: str,
    *,
    internal_id: str = "agent",
    **_kwargs,
) -> Optional[str]:
    if not is_tool_query(message):
        return None

    msg = message.strip()

    # --- show / list ---
    if re.search(r"\b(show|list|display|view)\b.*\bcanvas\b", msg, re.I):
        nodes = get_nodes()
        if not nodes:
            return "🎨 Canvas is empty. Use *add to canvas: your text* to add a node."
        lines = ["🎨 *Live Canvas* — current nodes:"]
        for n in nodes:
            lines.append(f"  • [{n['type']}] **{n['title']}** — {n['content'][:80]}")
        return "\n".join(lines)

    # --- clear ---
    if re.search(r"\bclear\b.*\bcanvas\b", msg, re.I):
        count = clear_canvas()
        return f"🎨 Canvas cleared ({count} node{'s' if count != 1 else ''} removed)."

    # --- remove ---
    m = _REMOVE_RE.search(msg)
    if m:
        nid = m.group("id").strip().lower().replace(" ", "-")
        ok = remove_node(nid)
        if ok:
            return f"🎨 Canvas node **{nid}** removed."
        return f"🎨 No canvas node with id **{nid}** found."

    # --- update ---
    m = _UPDATE_RE.search(msg)
    if m:
        nid = m.group("id").strip().lower().replace(" ", "-")
        content = m.group("content").strip()
        nodes = get_nodes()
        existing = next((n for n in nodes if n["id"] == nid), None)
        if not existing:
            return f"🎨 No canvas node with id **{nid}** found."
        add_node(
            title=existing["title"],
            content=content,
            node_type=existing["type"],
            node_id=nid,
            x=existing["x"],
            y=existing["y"],
            color=existing.get("color", ""),
            author=internal_id,
        )
        return f"🎨 Canvas node **{nid}** updated."

    # --- add ---
    m = _ADD_RE.search(msg)
    if m:
        raw_type = (m.group("type") or m.group("type2") or "text").lower()
        node_type = _TYPE_MAP.get(raw_type, "text")
        content = (
            m.group("content")
            or m.group("content2")
            or m.group("content3")
            or ""
        ).strip()
        # Use first 40 chars of content as title
        title = content[:40].replace("\n", " ") if content else "Untitled"
        node = add_node(title, content, node_type=node_type, author=internal_id)
        return f"🎨 Canvas node **{node['id']}** added ({node_type})."

    # Fallback: treat the whole message as content to add
    content = re.sub(r"^(canvas|add to canvas)[:\-–]?\s*", "", msg, flags=re.I).strip()
    if content:
        title = content[:40].replace("\n", " ")
        node = add_node(title, content, author=internal_id)
        return f"🎨 Canvas node **{node['id']}** added."

    return None
