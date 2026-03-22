import os
import uuid
import re
import argparse
from pathlib import Path
from typing import Union


class UUIDManager:
    def __init__(self):
        self.env_file = Path(".env")

    def generate_uuid(self) -> str:
        """Generate a fresh UUID"""
        return str(uuid.uuid4())

    def print_uuid(self) -> str:
        """Just print the UUID to console"""
        new_uuid = self.generate_uuid()
        return f"""
🎯 Generated UUID:
MASTER_USER_ID={new_uuid}
"""

    def update_env_file(self) -> str:
        """Update or create MASTER_USER_ID in .env file"""
        new_uuid = self.generate_uuid()

        try:
            # Read existing content if file exists
            if self.env_file.exists():
                content = self.env_file.read_text()

                # Check if MASTER_USER_ID already exists
                if "MASTER_USER_ID=" in content:
                    # Replace existing UUID
                    new_content = re.sub(
                        r"MASTER_USER_ID=[\w\-]+", f"MASTER_USER_ID={new_uuid}", content
                    )
                else:
                    # Add new UUID at the end
                    new_content = f"{content.rstrip()}\nMASTER_USER_ID={new_uuid}\n"
            else:
                # Create new file with UUID
                new_content = f"MASTER_USER_ID={new_uuid}\n"

            # Write the content
            self.env_file.write_text(new_content)

            return f"""
🎉 Success! 
📝 Updated: {self.env_file}
🆔 MASTER_USER_ID={new_uuid}
"""

        except Exception as e:
            return f"""
❌ Error: Couldn't update {self.env_file}
💢 Details: {str(e)}
🆔 Generated UUID (not saved): {new_uuid}
"""


def main():
    # Create argument parser with some style 😎
    parser = argparse.ArgumentParser(
        description="🎲 UUID Generator - Generate or update MASTER_USER_ID",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python gen_master_id.py           # Just print UUID
  python gen_master_id.py --env     # Update .env file
        """,
    )

    # Add the --env flag
    parser.add_argument(
        "--env",
        action="store_true",
        help="Update UUID in .env file (default: just print)",
    )

    args = parser.parse_args()
    manager = UUIDManager()

    # Choose action based on argument
    if args.env:
        print(manager.update_env_file())
    else:
        print(manager.print_uuid())


if __name__ == "__main__":
    main()
