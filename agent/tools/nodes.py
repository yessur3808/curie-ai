# agent/tools/nodes.py
"""
Nodes tool — visual node-graph / workflow builder.

Nodes are typed, directed-graph elements that represent steps in an
agent workflow, data-pipeline, or decision tree.  The graph is
persisted to ``~/.curie/nodes.json``.

Node schema
-----------
{
    "id":        str,    # unique slug
    "label":     str,    # display name
    "type":      str,    # "start" | "step" | "decision" | "end" | "action"
    "content":   str,    # description or code
    "edges":     list[str],  # outgoing node IDs
    "metadata":  dict,
    "created_at": float,
    "updated_at": float,
}

Natural-language triggers
--------------------------
  "add node: validate_input"
  "connect node a to node b"
  "show nodes"
  "list workflow nodes"
  "remove node validate_input"
  "clear nodes"
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "nodes"

_CURIE_DIR = Path.home() / ".curie"
_NODES_FILE = _CURIE_DIR / "nodes.json"
_lock = threading.Lock()

# ── Intent ───────────────────────────────────────────────────────────────────

_NODES_KEYWORDS = re.compile(
    r"\bnode(s)?\b|\bworkflow\s+node|\bgraph\s+node|\badd\s+node\b",
    re.IGNORECASE,
)


def is_tool_query(message: str) -> bool:
    return bool(_NODES_KEYWORDS.search(message))


# ── Persistence ───────────────────────────────────────────────────────────────


def _load() -> dict:
    try:
        return json.loads(_NODES_FILE.read_text())
    except Exception:
        return {"nodes": {}}


def _save(data: dict) -> None:
    _CURIE_DIR.mkdir(parents=True, exist_ok=True)
    _NODES_FILE.write_text(json.dumps(data, indent=2))


# ── Public API ────────────────────────────────────────────────────────────────


def get_nodes() -> list[dict]:
    data = _load()
    return list(data.get("nodes", {}).values())


def add_node(
    node_id: str,
    label: str,
    node_type: str = "step",
    content: str = "",
) -> dict:
    with _lock:
        data = _load()
        nodes = data.setdefault("nodes", {})
        nid = re.sub(r"[^a-z0-9_-]", "_", node_id.lower())
        now = time.time()
        node = {
            "id": nid,
            "label": label,
            "type": node_type,
            "content": content,
            "edges": nodes.get(nid, {}).get("edges", []),
            "metadata": nodes.get(nid, {}).get("metadata", {}),
            "created_at": nodes.get(nid, {}).get("created_at", now),
            "updated_at": now,
        }
        nodes[nid] = node
        _save(data)
        return node


def connect_nodes(from_id: str, to_id: str) -> bool:
    """Add a directed edge from *from_id* → *to_id*. Returns True on success."""
    with _lock:
        data = _load()
        nodes = data.get("nodes", {})
        fid = re.sub(r"[^a-z0-9_-]", "_", from_id.lower())
        tid = re.sub(r"[^a-z0-9_-]", "_", to_id.lower())
        if fid not in nodes or tid not in nodes:
            return False
        edges: list = nodes[fid].setdefault("edges", [])
        if tid not in edges:
            edges.append(tid)
        nodes[fid]["updated_at"] = time.time()
        _save(data)
        return True


def remove_node(node_id: str) -> bool:
    with _lock:
        data = _load()
        nodes = data.get("nodes", {})
        nid = re.sub(r"[^a-z0-9_-]", "_", node_id.lower())
        if nid not in nodes:
            return False
        del nodes[nid]
        # Remove dangling edges
        for n in nodes.values():
            edges = n.get("edges", [])
            if nid in edges:
                edges.remove(nid)
        _save(data)
        return True


def clear_nodes() -> int:
    with _lock:
        data = _load()
        count = len(data.get("nodes", {}))
        data["nodes"] = {}
        _save(data)
        return count


# ── Intent parsing ────────────────────────────────────────────────────────────

_ADD_RE = re.compile(
    r"add\s+(?:a\s+)?(?:(?P<type>start|step|decision|end|action)\s+)?node\s*[:\-–]?\s*[\"']?(?P<id>[a-z0-9_\- ]+)[\"']?"
    r"(?:\s+[:\-–]\s*(?P<label>.+))?",
    re.IGNORECASE,
)

_CONNECT_RE = re.compile(
    r"connect\s+(?:node\s+)?[\"']?(?P<from>[a-z0-9_\- ]+)[\"']?\s+"
    r"(?:to\s+(?:node\s+)?)[\"']?(?P<to>[a-z0-9_\- ]+)[\"']?",
    re.IGNORECASE,
)

_REMOVE_RE = re.compile(
    r"(?:remove|delete)\s+(?:node\s+)[\"']?(?P<id>[a-z0-9_\- ]+)[\"']?",
    re.IGNORECASE,
)


async def handle_tool_query(
    message: str,
    **_kwargs,
) -> Optional[str]:
    if not is_tool_query(message):
        return None

    msg = message.strip()

    # --- list / show ---
    if re.search(r"\b(show|list|display|view)\b.*(node|workflow|graph)", msg, re.I):
        nodes = get_nodes()
        if not nodes:
            return "🔗 No workflow nodes yet. Use *add node: my_step* to create one."
        lines = ["🔗 *Workflow Nodes:*"]
        for n in nodes:
            edges_str = " → " + ", ".join(n["edges"]) if n.get("edges") else ""
            lines.append(
                f"  • [{n['type']}] **{n['id']}** ({n['label']}){edges_str}"
            )
        return "\n".join(lines)

    # --- clear ---
    if re.search(r"\bclear\b.*(node|workflow|graph)", msg, re.I):
        count = clear_nodes()
        return f"🔗 Cleared {count} node{'s' if count != 1 else ''}."

    # --- connect ---
    m = _CONNECT_RE.search(msg)
    if m:
        fid = m.group("from").strip().lower().replace(" ", "_")
        tid = m.group("to").strip().lower().replace(" ", "_")
        ok = connect_nodes(fid, tid)
        if ok:
            return f"🔗 Edge **{fid}** → **{tid}** created."
        return f"🔗 One or both nodes not found: **{fid}**, **{tid}**."

    # --- remove ---
    m = _REMOVE_RE.search(msg)
    if m:
        nid = m.group("id").strip().lower().replace(" ", "_")
        ok = remove_node(nid)
        return f"🔗 Node **{nid}** {'removed' if ok else 'not found'}."

    # --- add ---
    m = _ADD_RE.search(msg)
    if m:
        nid = m.group("id").strip().lower().replace(" ", "_")
        label = (m.group("label") or m.group("id")).strip()
        node_type = (m.group("type") or "step").lower()
        node = add_node(nid, label, node_type=node_type)
        return f"🔗 Node **{node['id']}** ({node['type']}) added."

    return None
