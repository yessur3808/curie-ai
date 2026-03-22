import os
import json
from datetime import datetime
from typing import Set, List, Dict
from pathlib import Path


class DirectoryStructureGenerator:
    def __init__(self):
        self.default_ignore_patterns = {
            # Virtual Environments - Complete patterns
            "venv",
            "venv/**",
            ".venv",
            ".venv/**",
            "env",
            "env/**",
            ".env",
            ".env/**",
            "virtualenv",
            "virtualenv/**",
            # Build and Cache
            "__pycache__",
            "*.pyc",
            "build",
            "dist",
            "*.egg-info",
            # IDE and Git
            ".git",
            ".idea",
            ".vscode",
            # System
            ".DS_Store",
            "Thumbs.db",
        }

        self.project_extensions = {
            ".py",
            ".md",
            ".txt",
            ".yml",
            ".yaml",
            ".json",
            ".ini",
            ".cfg",
            ".toml",
            ".rst",
        }

    def generate_structure(self, startpath: str) -> str:
        self.md_content = [
            "# 🚀 Project Structure",
            f"\n📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        ]
        self.structure_dict = {}

        def is_ignored(path: Path) -> bool:
            # Check if any parent directory is a venv
            parents = path.parents
            for parent in parents:
                if parent.name.lower() in {
                    "venv",
                    ".venv",
                    "env",
                    ".env",
                    "virtualenv",
                }:
                    return True

            return any(
                str(path).lower().endswith(ignore.lower())
                for ignore in self.default_ignore_patterns
            )

        def process_directory(
            path: Path, indent: str = "", current_dict: Dict = None
        ) -> None:
            if current_dict is None:
                current_dict = self.structure_dict

            # Skip if this directory should be ignored
            if is_ignored(path):
                return

            self.md_content.append(f"\n{indent}📁 {path.name}")
            current_dict.update(
                {"type": "directory", "name": path.name, "contents": []}
            )

            try:
                # Get and sort directory contents
                entries = sorted(
                    [x for x in path.iterdir() if not is_ignored(x)],
                    key=lambda x: (not x.is_dir(), x.name.lower()),
                )

                for entry in entries:
                    if entry.is_file():
                        # Skip files we don't care about
                        if not any(
                            entry.name.endswith(ext) for ext in self.project_extensions
                        ):
                            continue

                        # Add appropriate emoji based on file type
                        emoji = {
                            ".py": "🐍",
                            ".md": "📝",
                            ".txt": "📝",
                            ".yml": "⚙️",
                            ".yaml": "⚙️",
                            ".json": "⚙️",
                            ".toml": "⚙️",
                            ".ini": "⚙️",
                            ".cfg": "⚙️",
                            ".rst": "📝",
                        }.get(entry.suffix, "📄")

                        self.md_content.append(f"{indent}  {emoji} {entry.name}")
                        current_dict["contents"].append(
                            {"type": "file", "name": entry.name}
                        )
                    elif entry.is_dir():
                        new_dict = {}
                        current_dict["contents"].append(new_dict)
                        process_directory(entry, indent + "  ", new_dict)

            except PermissionError:
                self.md_content.append(f"{indent}  ⚠️ Access Denied")

        # Start processing from the root
        process_directory(Path(startpath))

        # Save the results
        with open("directory_structure.md", "w", encoding="utf-8") as md_file:
            md_file.write("\n".join(self.md_content))

        with open("directory_structure.json", "w", encoding="utf-8") as json_file:
            json.dump(self.structure_dict, json_file, indent=2)

        return "🎉 Directory structure generated! Check 'directory_structure.md' and 'directory_structure.json'"


# Super simple usage
if __name__ == "__main__":
    generator = DirectoryStructureGenerator()
    print(generator.generate_structure("./"))
