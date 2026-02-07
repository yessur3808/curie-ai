#!/usr/bin/env python3
"""
.env File Sync Utility

This script helps manage your .env file by:
1. Comparing .env with .env.example to find missing variables
2. Adding missing variables to .env without overwriting existing values
3. Identifying variables in .env that are no longer in .env.example (potentially obsolete)

Usage:
    python scripts/sync_env.py                    # Check differences and get interactive prompts
    python scripts/sync_env.py --check            # Only check and display differences
    python scripts/sync_env.py --sync             # Automatically add missing variables (safe, won't overwrite)
    python scripts/sync_env.py --clean            # Interactive removal of obsolete variables
    python scripts/sync_env.py --backup           # Create backup before making changes
"""

import os
import sys
import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Set


class EnvManager:
    """Manages .env file synchronization with .env.example"""
    
    def __init__(self, repo_root: str = None):
        if repo_root is None:
            # Try to find repository root
            current = Path(__file__).resolve().parent.parent
            self.repo_root = current
        else:
            self.repo_root = Path(repo_root)
        
        self.env_example_path = self.repo_root / ".env.example"
        self.env_path = self.repo_root / ".env"
        
    def parse_env_file(self, filepath: Path) -> Dict[str, Tuple[str, List[str], bool]]:
        """
        Parse .env file and return dict of {variable_name: (value, [comment_lines], is_commented)}
        
        Returns:
            Dict mapping variable names to (value, comments, is_commented) tuples
        """
        if not filepath.exists():
            return {}
        
        env_vars = {}
        current_comments = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\n')
                
                # Skip empty lines
                if not line.strip():
                    current_comments = []
                    continue
                
                # Collect comment lines
                if line.strip().startswith('#'):
                    current_comments.append(line)
                    continue
                
                # Parse variable assignment
                if '=' in line:
                    # Handle both "VAR=value" and "# VAR=value" (commented out vars)
                    is_commented = line.strip().startswith('#')
                    clean_line = line.strip().lstrip('#').strip()
                    
                    if '=' in clean_line:
                        key, value = clean_line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Store with comments and whether it's commented out
                        env_vars[key] = (value, current_comments.copy(), is_commented)
                        current_comments = []
        
        return env_vars
    
    def get_missing_variables(self) -> List[Tuple[str, str, List[str]]]:
        """
        Get list of variables in .env.example but not in .env
        
        Returns:
            List of (variable_name, default_value, comments) tuples
        """
        example_vars = self.parse_env_file(self.env_example_path)
        env_vars = self.parse_env_file(self.env_path)
        
        missing = []
        for key, (value, comments, is_commented) in example_vars.items():
            if key not in env_vars:
                missing.append((key, value, comments))
        
        return missing
    
    def get_obsolete_variables(self) -> List[Tuple[str, str]]:
        """
        Get list of variables in .env but not in .env.example
        
        Returns:
            List of (variable_name, current_value) tuples
        """
        example_vars = self.parse_env_file(self.env_example_path)
        env_vars = self.parse_env_file(self.env_path)
        
        obsolete = []
        for key, (value, comments, is_commented) in env_vars.items():
            if key not in example_vars and not is_commented:
                obsolete.append((key, value))
        
        return obsolete
    
    def create_backup(self) -> Path:
        """Create a backup of the current .env file"""
        if not self.env_path.exists():
            print("⚠️  No .env file exists to backup")
            return None
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.repo_root / f".env.backup.{timestamp}"
        
        shutil.copy2(self.env_path, backup_path)
        print(f"✅ Backup created: {backup_path}")
        return backup_path
    
    def add_missing_variables(self, dry_run: bool = False) -> int:
        """
        Add missing variables to .env file
        
        Args:
            dry_run: If True, only show what would be added without modifying file
            
        Returns:
            Number of variables added
        """
        missing = self.get_missing_variables()
        
        if not missing:
            print("✅ All variables from .env.example are already in .env")
            return 0
        
        if dry_run:
            print(f"\n📋 Would add {len(missing)} missing variable(s):")
            for key, value, comments in missing:
                print(f"   {key}={value}")
            return 0
        
        # Append missing variables to .env
        with open(self.env_path, 'a', encoding='utf-8') as f:
            f.write("\n# ===== Variables added by sync_env.py =====\n")
            f.write(f"# Added on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            for key, value, comments in missing:
                # Write comments
                for comment in comments:
                    f.write(f"{comment}\n")
                
                # Write variable
                f.write(f"{key}={value}\n")
                f.write("\n")
        
        print(f"✅ Added {len(missing)} missing variable(s) to .env")
        return len(missing)
    
    def remove_obsolete_variables(self, variables_to_remove: Set[str]) -> int:
        """
        Remove specified obsolete variables from .env
        
        Args:
            variables_to_remove: Set of variable names to remove
            
        Returns:
            Number of variables removed
        """
        if not self.env_path.exists():
            print("⚠️  No .env file exists")
            return 0
        
        if not variables_to_remove:
            return 0
        
        # Read current .env
        with open(self.env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Filter out obsolete variables
        new_lines = []
        skip_next_empty = False
        
        for line in lines:
            # Check if this line defines a variable to remove
            if '=' in line and not line.strip().startswith('#'):
                key = line.split('=', 1)[0].strip()
                if key in variables_to_remove:
                    skip_next_empty = True
                    continue
            
            # Skip empty line after removed variable
            if skip_next_empty and not line.strip():
                skip_next_empty = False
                continue
            
            new_lines.append(line)
        
        # Write back
        with open(self.env_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        
        print(f"✅ Removed {len(variables_to_remove)} obsolete variable(s) from .env")
        return len(variables_to_remove)
    
    def print_status(self):
        """Print current status of .env vs .env.example"""
        print("\n" + "=" * 70)
        print("📊 .env FILE STATUS")
        print("=" * 70)
        
        # Check if files exist
        if not self.env_example_path.exists():
            print("❌ .env.example not found!")
            return
        
        if not self.env_path.exists():
            print("⚠️  .env file does not exist")
            print("   Run with --sync to create it from .env.example")
            return
        
        # Get missing and obsolete variables
        missing = self.get_missing_variables()
        obsolete = self.get_obsolete_variables()
        
        # Print summary
        print(f"\n📁 Files:")
        print(f"   .env.example: {self.env_example_path}")
        print(f"   .env:         {self.env_path}")
        
        # Missing variables
        if missing:
            print(f"\n⚠️  Missing Variables ({len(missing)}):")
            print("   These are in .env.example but not in your .env:")
            for key, value, comments in missing:
                # Show comment if available
                if comments:
                    last_comment = comments[-1].strip().lstrip('#').strip()
                    if len(last_comment) > 50:
                        last_comment = last_comment[:47] + "..."
                    print(f"   • {key}  # {last_comment}")
                else:
                    print(f"   • {key}")
        else:
            print(f"\n✅ No missing variables")
        
        # Obsolete variables
        if obsolete:
            print(f"\n🗑️  Potentially Obsolete Variables ({len(obsolete)}):")
            print("   These are in your .env but not in .env.example:")
            for key, value in obsolete:
                print(f"   • {key}")
        else:
            print(f"\n✅ No obsolete variables")
        
        # Actions
        print("\n💡 Suggested Actions:")
        if missing:
            print("   • Run with --sync to add missing variables")
        if obsolete:
            print("   • Run with --clean to interactively remove obsolete variables")
        if not missing and not obsolete:
            print("   • Your .env is in sync with .env.example!")
        
        print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Sync .env file with .env.example",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--check', action='store_true',
                        help='Only check and display differences')
    parser.add_argument('--sync', action='store_true',
                        help='Add missing variables to .env (safe, won\'t overwrite)')
    parser.add_argument('--clean', action='store_true',
                        help='Interactively remove obsolete variables')
    parser.add_argument('--backup', action='store_true',
                        help='Create backup before making changes')
    parser.add_argument('--repo-root', type=str,
                        help='Repository root directory (default: auto-detect)')
    
    args = parser.parse_args()
    
    # Initialize manager
    manager = EnvManager(args.repo_root)
    
    # Check if .env.example exists
    if not manager.env_example_path.exists():
        print(f"❌ Error: .env.example not found at {manager.env_example_path}")
        sys.exit(1)
    
    # If no .env exists and not syncing, just show status
    if not manager.env_path.exists() and not args.sync:
        print("⚠️  .env file does not exist")
        print("   Create it by running: python scripts/sync_env.py --sync")
        sys.exit(0)
    
    # Backup if requested
    if args.backup and manager.env_path.exists():
        manager.create_backup()
    
    # Sync mode - add missing variables
    if args.sync:
        # Create .env if it doesn't exist
        if not manager.env_path.exists():
            print("📝 Creating .env from .env.example...")
            shutil.copy2(manager.env_example_path, manager.env_path)
            print(f"✅ Created .env at {manager.env_path}")
            print("   Please review and update the values as needed.")
        else:
            if args.backup:
                manager.create_backup()
            manager.add_missing_variables(dry_run=False)
        return
    
    # Clean mode - remove obsolete variables
    if args.clean:
        obsolete = manager.get_obsolete_variables()
        
        if not obsolete:
            print("✅ No obsolete variables found in .env")
            return
        
        print(f"\n🗑️  Found {len(obsolete)} potentially obsolete variable(s):")
        print("   (These are in your .env but not in .env.example)\n")
        
        to_remove = set()
        for key, value in obsolete:
            print(f"   {key}={value}")
            response = input(f"   Remove {key}? [y/N]: ").strip().lower()
            if response == 'y':
                to_remove.add(key)
        
        if to_remove:
            if args.backup:
                manager.create_backup()
            manager.remove_obsolete_variables(to_remove)
        else:
            print("\n✅ No variables removed")
        return
    
    # Default - just show status
    manager.print_status()


if __name__ == "__main__":
    main()
