# utils/project_indexer.py

import os
from pathlib import Path

from llm import manager
from utils.ttl_cache import TTLCache

_PROJECT_INDEX_CACHE = TTLCache(
    ttl_seconds=300,
    max_size=32,
    name="project_indexes",
    owner_scope="user",
    invalidation_event="Any indexed path, size, or modification-time change",
    sensitivity="personal",
)
_IGNORED = {".git", ".venv", "node_modules", "__pycache__", "models"}
_SENSITIVE_NAMES = {".env", ".netrc", "credentials", "credentials.json", "id_rsa"}


def _project_signature(root: Path) -> tuple:
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED)
        for name in sorted(filenames):
            if name.casefold() in _SENSITIVE_NAMES:
                continue
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return tuple(rows)


def classify_project_intent(message):
    # You can start with simple rules for quick testing
    msg = message.lower()
    if "index my project" in msg or "scan my project" in msg:
        return "index_project"
    if "create new project" in msg or "make a new project" in msg:
        return "create_project"
    if "show project" in msg or "project tree" in msg:
        return "show_project"
    if ("help" in msg or "advice" in msg) and "project" in msg:
        return "project_help"
    # If no match, use LLM for fallback
    prompt = (
        f"User: {message}\n\n"
        "Classify the user's intent as one of: index_project, create_project, project_help, show_project, or none. "
        "Return only the intent keyword."
    )
    intent = manager.ask_llm(prompt, temperature=0.0, max_tokens=5)
    return intent.strip()


def index_project_dir(
    root_path,
    max_preview_lines=10,
    include_content_types=(".md", ".py", ".txt"),
    *,
    owner_id: str | None = None,
):
    root = Path(root_path).resolve()
    signature = _project_signature(root)
    cache_key = (str(root), signature, max_preview_lines, tuple(include_content_types))
    if owner_id:
        cached = _PROJECT_INDEX_CACHE.get(cache_key, owner_id=owner_id)
        if cached is not None:
            return cached
    project_index = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED)
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        project_index[rel_dir] = []
        for fname in sorted(filenames):
            if fname.casefold() in _SENSITIVE_NAMES:
                continue
            rel_file = os.path.join(rel_dir, fname) if rel_dir else fname
            file_info = {"name": fname, "rel_path": rel_file}
            ext = os.path.splitext(fname)[-1]
            if ext in include_content_types:
                try:
                    with open(os.path.join(dirpath, fname), "r", encoding="utf-8") as f:
                        preview = "".join(
                            line for _, line in zip(range(max_preview_lines), f)
                        )
                        file_info["preview"] = preview
                except Exception:
                    file_info["preview"] = "[Could not read file]"
            project_index[rel_dir].append(file_info)
    if owner_id:
        _PROJECT_INDEX_CACHE.set(cache_key, project_index, owner_id=owner_id)
    return project_index


def project_index_markdown(index):
    md = "# 📁 Project Index\n"
    for folder, files in sorted(index.items()):
        md += f"\n**/{folder if folder else '.'}/**\n"
        for fi in files:
            md += f"- `{fi['rel_path']}`"
            if "preview" in fi:
                md += f"\n  <details><summary>Preview</summary>\n\n```\n{fi['preview']}\n```\n</details>\n"
            else:
                md += "\n"
    return md
