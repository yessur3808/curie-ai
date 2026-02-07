# Enhanced Coding Modules Guide

This guide covers the enhanced coding functionality in Curie AI, including code review, multi-platform PR/MR management, self-update capabilities, and the standalone coding service.

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Setup](#setup)
4. [Code Review](#code-review)
5. [Multi-Platform Support](#multi-platform-support)
6. [Self-Update System](#self-update-system)
7. [Standalone Coding Service](#standalone-coding-service)
8. [Usage Examples](#usage-examples)

## Overview

The enhanced coding modules provide comprehensive code management capabilities:

- **Code Review**: Automated code review with LLM-powered analysis
- **Multi-Platform PR/MR**: Support for GitHub, GitLab, and Bitbucket
- **Self-Update**: Safe self-update mechanism with rollback capability
- **Standalone Service**: Run coding operations in parallel with the main assistant

## Features

### Code Review
- Review individual files, diffs, or entire pull requests
- Detect potential bugs, security issues, and code quality problems
- Provide actionable suggestions for improvement
- Generate formatted markdown review comments
- Score code quality (0-10 scale)

### Multi-Platform Support

#### GitHub
- Create and update pull requests
- Add review comments and suggestions
- Lint results automatically posted
- Full integration with existing coder module

#### GitLab
- Create and update merge requests
- Automated code review on MRs
- Support for self-hosted GitLab instances
- Project path detection from URLs

#### Bitbucket
- Create and update pull requests
- Automated code review
- Workspace and repository detection
- App password authentication

### Self-Update
- Check for available updates
- Pull latest code from repository
- Update Python dependencies
- Create automatic backups before updating
- Rollback capability if update fails
- Optional service restart

### Standalone Coding Service
- Runs independently in a separate thread
- Task queue for asynchronous operations
- Notification system to master user
- Supports all code operations (review, PR/MR creation, updates)
- Can run in parallel with main assistant

## Setup

### Basic Configuration

Add the following to your `.env` file:

```env
# GitHub (already supported)
GITHUB_TOKEN=ghp_your_token_here
MAIN_REPO=https://github.com/username/repository
MAIN_REVIEWER=reviewer_username
TARGET_BRANCH=main

# GitLab (optional)
GITLAB_TOKEN=glpat_your_token_here
GITLAB_URL=https://gitlab.com  # or your self-hosted instance

# Bitbucket (optional)
BITBUCKET_USERNAME=your_username
BITBUCKET_APP_PASSWORD=your_app_password

# Self-Update (optional)
SYSTEMD_SERVICE_NAME=curie-ai  # for automated restarts

# Enable coding service
RUN_CODING_SERVICE=true
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

The new dependencies include:
- `python-gitlab` - GitLab API support
- `requests` - HTTP client for API calls

### Platform-Specific Setup

#### GitHub
1. Create a personal access token at https://github.com/settings/tokens
2. Required scopes: `repo`, `workflow`
3. Set `GITHUB_TOKEN` in `.env`

#### GitLab
1. Create a personal access token at GitLab Settings → Access Tokens
2. Required scopes: `api`, `write_repository`
3. Set `GITLAB_TOKEN` in `.env`
4. For self-hosted: Set `GITLAB_URL` to your instance URL

#### Bitbucket
1. Create an app password at Bitbucket Settings → App passwords
2. Required permissions: `Repositories: Read, Write`, `Pull requests: Read, Write`
3. Set `BITBUCKET_USERNAME` and `BITBUCKET_APP_PASSWORD` in `.env`

## Code Review

### Using the Code Reviewer

```python
from agent.skills.code_reviewer import CodeReviewer

reviewer = CodeReviewer()

# Review a file
result = reviewer.review_file('path/to/file.py', repo_path='.')

# Review a diff
diff = "..."  # Git diff content
result = reviewer.review_code_changes(diff, file_path='file.py')

# Review a pull request
result = reviewer.review_pull_request('.', 'main', 'feature-branch')

# Format review as comment
comment = reviewer.format_review_comment(result)
```

### Review Result Structure

```python
{
    'score': 8.5,  # Quality score 0-10
    'issues': [
        'Potential null pointer exception on line 42',
        'Missing input validation for user data'
    ],
    'suggestions': [
        'Consider adding error handling',
        'Extract magic numbers into constants'
    ],
    'summary': 'Overall code quality is good with minor improvements needed'
}
```

## Multi-Platform Support

### GitLab Integration

```python
from agent.skills.gitlab_integration import GitLabIntegration, apply_gitlab_code_change

# Initialize
gitlab = GitLabIntegration()

# Create merge request
mr_data = gitlab.create_merge_request(
    project_path='namespace/project',
    source_branch='feature',
    target_branch='main',
    title='Feature: New functionality',
    description='Description here'
)

# Review merge request
review = gitlab.review_merge_request('namespace/project', mr_iid=123)

# Apply code changes with MR creation
branch, changes, mr_url = apply_gitlab_code_change(
    goal='Improve error handling',
    files_to_edit=['file1.py', 'file2.py'],
    repo_path='/path/to/repo',
    branch_name='feature/error-handling'
)
```

### Bitbucket Integration

```python
from agent.skills.bitbucket_integration import BitbucketIntegration, apply_bitbucket_code_change

# Initialize
bitbucket = BitbucketIntegration()

# Create pull request
pr_data = bitbucket.create_pull_request(
    workspace='workspace-name',
    repo_slug='repository-slug',
    source_branch='feature',
    destination_branch='main',
    title='Feature: New functionality',
    description='Description here'
)

# Review pull request
review = bitbucket.review_pull_request('workspace', 'repo-slug', pr_id=123)

# Apply code changes with PR creation
branch, changes, pr_url = apply_bitbucket_code_change(
    goal='Improve error handling',
    files_to_edit=['file1.py', 'file2.py'],
    repo_path='/path/to/repo',
    branch_name='feature/error-handling'
)
```

## Self-Update System

### Using Self-Updater

```python
from agent.skills.self_updater import SelfUpdater, auto_update

# Check for updates
updater = SelfUpdater()
check = updater.check_for_updates('main')

if check['updates_available']:
    print(f"Updates available: {check['commits_behind']} commits")
    print("Changes:")
    for change in check['changes']:
        print(change)

# Perform update
result = updater.full_update(
    branch='main',
    update_deps=True,
    force=False
)

# Or use convenience function
result = auto_update(
    branch='main',
    update_deps=True,
    restart=False,  # Set to True to restart service
    force=False
)
```

### Update Process

1. **Check for updates**: Fetches latest from remote and compares
2. **Create backup**: Automatic backup branch created
3. **Pull changes**: Git pull from target branch
4. **Update dependencies**: Runs `pip install -r requirements.txt`
5. **Restart (optional)**: Restart service via systemd

### Rollback

```python
# If something goes wrong
updater.rollback()
```

## Standalone Coding Service

### Starting the Service

#### Via main.py

```bash
# Run with other services
python main.py --coding-service --api

# Or set in .env
RUN_CODING_SERVICE=true
python main.py
```

#### Standalone

```bash
python services/coding_service.py
```

### Using the Service

```python
from services.coding_service import CodingService

# Define notification callback
def notify_master(message, data):
    print(f"Notification: {message}")
    print(f"Data: {data}")

# Create and start service
service = CodingService(notification_callback=notify_master)
service.start()

# Add tasks
task_id = service.add_task('review', {
    'type': 'file',
    'file_path': 'main.py',
    'repo_path': '.'
})

task_id = service.add_task('create_pr', {
    'platform': 'github',
    'goal': 'Add new feature',
    'files': ['file1.py', 'file2.py'],
    'repo_path': '/path/to/repo',
    'branch': 'feature/new-feature'
})

task_id = service.add_task('self_update', {
    'branch': 'main',
    'update_deps': True,
    'restart': False
})

# Check status
status = service.get_status()
print(status)

# Stop service
service.stop()
```

### Task Types

- **review**: Code review tasks
  - `type`: 'file', 'diff', or 'pr'
  - Platform-specific parameters

- **create_pr**: Create PR/MR with code changes
  - `platform`: 'github', 'gitlab', or 'bitbucket'
  - `goal`: Enhancement goal
  - `files`: Files to edit
  - `repo_path`: Repository path
  - `branch`: Branch name

- **self_update**: Update the system
  - `branch`: Target branch (default: 'main')
  - `update_deps`: Update dependencies (default: true)
  - `restart`: Restart service (default: false)
  - `force`: Force update discarding local changes (default: false)

## Usage Examples

### Example 1: Review a GitLab MR

```python
from agent.skills.gitlab_integration import GitLabIntegration

gitlab = GitLabIntegration()
review = gitlab.review_merge_request(
    project_path='mycompany/myproject',
    mr_iid=42,
    post_comment=True  # Automatically post review as comment
)

print(f"Score: {review['score']}/10")
print(f"Issues: {len(review['issues'])}")
```

### Example 2: Create PR on Multiple Platforms

```python
# Detect platform automatically
from services.coding_service import CodingService

service = CodingService()

repo_url = os.getenv('MAIN_REPO')
platform = service.detect_platform(repo_url)

task_id = service.add_task('create_pr', {
    'platform': platform,
    'goal': 'Refactor authentication module',
    'files': ['auth.py', 'middleware.py'],
    'repo_path': '/path/to/repo',
    'branch': 'refactor/auth'
})
```

### Example 3: Automated Self-Update

```python
from agent.skills.self_updater import auto_update

# Check and update if available
result = auto_update(
    branch='main',
    update_deps=True,
    restart=False
)

if result['success']:
    print("Update successful!")
    if result['check']['commits_behind'] > 0:
        print(f"Updated {result['check']['commits_behind']} commits")
else:
    print(f"Update failed: {result.get('error')}")
```

### Example 4: Parallel Operations

```python
from services.coding_service import CodingService
import time

service = CodingService()
service.start()

# Queue multiple tasks
tasks = []

# Review multiple PRs
tasks.append(service.add_task('review', {
    'type': 'pr',
    'platform': 'github',
    'project_path': 'user/repo',
    'mr_iid': 10
}))

tasks.append(service.add_task('review', {
    'type': 'pr',
    'platform': 'gitlab',
    'project_path': 'group/project',
    'mr_iid': 20
}))

# Wait for completion
time.sleep(5)

# Check status
status = service.get_status()
print(f"Queue size: {status['queue_size']}")
```

## Best Practices

1. **Code Review**
   - Review before merging to catch issues early
   - Use the score to gauge code quality trends
   - Address security-related issues first

2. **Multi-Platform**
   - Set up only the platforms you need
   - Use environment variables for configuration
   - Test with small changes first

3. **Self-Update**
   - Always test updates in a development environment first
   - Keep backups of important data
   - Monitor logs during and after updates
   - Use the rollback feature if issues occur

4. **Coding Service**
   - Use for long-running operations
   - Monitor task queue size
   - Set up proper notification callbacks
   - Handle errors gracefully

## Troubleshooting

### GitLab Connection Issues
- Verify `GITLAB_TOKEN` has correct permissions
- Check `GITLAB_URL` is correct (include protocol)
- For self-hosted: Ensure network access

### Bitbucket Authentication
- App passwords have limited lifetime
- Regenerate if expired
- Check username is correct (not email)

### Self-Update Failures
- Check git credentials and permissions
- Ensure clean working directory
- Verify internet connectivity
- Check disk space for dependencies

### Coding Service Not Starting
- Check for port conflicts
- Verify all dependencies installed
- Check log files for errors
- Ensure proper permissions

## Security Considerations

1. **Tokens and Credentials**
   - Never commit tokens to version control
   - Use environment variables
   - Rotate tokens regularly
   - Limit token permissions to minimum required

2. **Code Review**
   - Don't blindly apply all suggestions
   - Review security-related changes carefully
   - Test changes before merging

3. **Self-Update**
   - Only update from trusted sources
   - Review changes before applying
   - Keep backups
   - Use rollback if suspicious activity detected

4. **API Access**
   - Limit API access to necessary operations
   - Monitor API usage
   - Implement rate limiting
   - Log all operations

## Additional Resources

- [GitHub API Documentation](https://docs.github.com/en/rest)
- [GitLab API Documentation](https://docs.gitlab.com/ee/api/)
- [Bitbucket API Documentation](https://developer.atlassian.com/cloud/bitbucket/rest/)
- [GitPython Documentation](https://gitpython.readthedocs.io/)

## Support

For issues or questions:
1. Check the troubleshooting section
2. Review logs for error messages
3. Open an issue on GitHub
4. Consult the community

---

**Note**: These features require proper configuration and appropriate permissions. Test in a safe environment before deploying to production.
