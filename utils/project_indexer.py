# utils/project_indexer.py

import os

from llm import manager

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

def index_project_dir(root_path, max_preview_lines=10, include_content_types=('.md', '.py', '.txt')):
    project_index = {}
    for dirpath, dirnames, filenames in os.walk(root_path):
        rel_dir = os.path.relpath(dirpath, root_path)
        if rel_dir == ".":
            rel_dir = ""
        project_index[rel_dir] = []
        for fname in filenames:
            rel_file = os.path.join(rel_dir, fname) if rel_dir else fname
            file_info = {"name": fname, "rel_path": rel_file}
            ext = os.path.splitext(fname)[-1]
            if ext in include_content_types:
                try:
                    with open(os.path.join(dirpath, fname), "r", encoding="utf-8") as f:
                        preview = "".join([next(f) for _ in range(max_preview_lines)])
                        file_info["preview"] = preview
                except Exception:
                    file_info["preview"] = "[Could not read file]"
            project_index[rel_dir].append(file_info)
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