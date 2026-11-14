"""Shared personality contract for every externally visible generation path."""

from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any, Mapping

from utils.persona import normalize_persona

_PERSONA_ROOT = Path(__file__).resolve().parents[1] / "assets" / "personality"


@lru_cache(maxsize=8)
def _load_persona_file(filename: str) -> dict[str, Any]:
    """Load a repository persona quietly for background generation services."""
    safe_name = Path(filename).name
    if not safe_name.endswith(".json"):
        safe_name += ".json"
    path = _PERSONA_ROOT / safe_name
    if not path.is_file():
        path = _PERSONA_ROOT / "curie.json"
    with path.open(encoding="utf-8") as source:
        return normalize_persona(json.load(source))


def active_persona() -> dict[str, Any]:
    """Return the persona selected for the running Curie instance."""
    return _load_persona_file(os.getenv("PERSONA_FILE", "curie.json").strip())


def build_persona_contract(
    persona: Mapping[str, Any] | None = None,
    *,
    medium: str,
    structured_output: bool = False,
    compact: bool = False,
) -> str:
    """Create one authoritative identity contract for a public response medium.

    Length, formatting, and urgency may adapt to the task. Identity does not. For
    machine-readable output, the schema remains strict while Curie's judgment is
    reflected in any user-facing string fields.
    """
    selected = normalize_persona(dict(persona or active_persona()))
    system_prompt = str(selected.get("system_prompt") or "").strip()
    name = str(selected.get("name") or "Curie").strip()
    from agent.presentation import voice_spec

    identity = voice_spec(selected)
    format_rule = (
        "Return exactly the requested machine-readable structure with no extra prose."
        if structured_output
        else "Return only the externally visible content for this medium."
    )
    if compact:
        return identity + (" " + format_rule if structured_output else "")
    return (
        f"[ACTIVE PERSONA: {name}]\n{system_prompt}\n\n"
        f"[SHARED VOICE]\n{identity}\n"
        f"Medium: {medium}. Adapt to the task; never fall back to a generic assistant. "
        "Preserve facts, code, numbers, citations and requested output format. "
        "The user's language and exact-wording requests take precedence. "
        f"{format_rule}"
    )


def apply_persona_contract(
    task_prompt: str,
    *,
    medium: str,
    persona: Mapping[str, Any] | None = None,
    structured_output: bool = False,
    compact: bool = False,
) -> str:
    """Prefix a public generation task with the shared personality contract."""
    contract = build_persona_contract(
        persona,
        medium=medium,
        structured_output=structured_output,
        compact=compact,
    )
    separator = "\n" if compact else "\n\n[CURRENT TASK]\n"
    return contract + separator + str(task_prompt).strip()
