#!/usr/bin/env python3
"""
Example usage of the enhanced coding modules

This script demonstrates the structure of the new features.
Note: Requires dependencies from requirements.txt to be installed.
"""

import os

def main():
    """Show examples of how to use the enhanced coding modules"""
    print("=" * 60)
    print("Enhanced Coding Modules - Usage Examples")
    print("=" * 60)
    
    print("\n1. CODE REVIEW")
    print("-" * 40)
    print("""
from agent.skills.code_reviewer import CodeReviewer

reviewer = CodeReviewer()
result = reviewer.review_code_changes(diff_content, "file.py")
comment = reviewer.format_review_comment(result)
    """)
    
    print("\n2. GITLAB INTEGRATION")
    print("-" * 40)
    print("""
from agent.skills.gitlab_integration import GitLabIntegration

gitlab = GitLabIntegration()
mr_data = gitlab.create_merge_request(
    project_path='namespace/project',
    source_branch='feature',
    target_branch='main',
    title='New Feature',
    description='Description here'
)
    """)
    
    print("\n3. BITBUCKET INTEGRATION")
    print("-" * 40)
    print("""
from agent.skills.bitbucket_integration import BitbucketIntegration

bitbucket = BitbucketIntegration()
pr_data = bitbucket.create_pull_request(
    workspace='workspace',
    repo_slug='repo',
    source_branch='feature',
    destination_branch='main',
    title='New Feature',
    description='Description here'
)
    """)
    
    print("\n4. SELF-UPDATE")
    print("-" * 40)
    print("""
from agent.skills.self_updater import auto_update

result = auto_update(
    branch='main',
    update_deps=True,
    restart=False
)
    """)
    
    print("\n5. STANDALONE CODING SERVICE")
    print("-" * 40)
    print("""
from services.coding_service import CodingService

service = CodingService(notification_callback=my_callback)
service.start()

task_id = service.add_task('review', {
    'type': 'file',
    'file_path': 'main.py'
})

status = service.get_status()
    """)
    
    print("\n" + "=" * 60)
    print("For complete documentation, see:")
    print("  docs/CODING_MODULES_GUIDE.md")
    print("=" * 60)


if __name__ == '__main__':
    main()
