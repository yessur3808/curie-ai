# Contributing to Curie AI 🚀

First off, thanks for taking the time to contribute! ❤️

## Table of Contents
- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Process](#development-process)
- [Pull Request Process](#pull-request-process)
- [Style Guidelines](#style-guidelines)
- [Community](#community)

## Code of Conduct
This project and everyone participating in it is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

### Prerequisites
- Python 3.10+
- pip
- virtualenv

### Setup Steps
1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/yessur3808/curie-ai
   cd project-name

3. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

Install dependencies:
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Development dependencies
```

## Development Process
### Making Changes
1. Create a new branch:
```bash
git checkout -b feature/your-feature-name
```

2. Make your changes
3. Run tests:
```bash
pytest
```

4. Update documentation if needed


## Commit Guidelines
- Use clear, descriptive commit messages
- Format: `type(scope): description`
- Types: feat, fix, docs, style, refactor, test, chore
- Example: `feat: add OAuth2 authentication`


## Pull Request Process
1. Update the README.md with details of changes if needed
2. Update the documentation
3. Add tests for new features
4. Ensure all tests pass
5. Get review from maintainers
6. Squash commits if requested


### PR Title Format
- Format: `type(scope): description`
- Example: `feat: add new endpoint for user profiles`


## Style Guidelines
### Python Style
- Follow PEP 8
- Use type hints
- Maximum line length: 88 characters (Black formatter)
- Use docstrings for functions and classes


### Documentation Style
- Clear and concise
- Include examples
- Update relevant docs with changes

### Testing
- Write unit tests for new features
- Maintain or improve code coverage
- Test edge cases


## Community
### Ways to Contribute
- Submit bugs and feature requests
- Review Pull Requests
- Improve documentation
- Write tutorials or blog posts
- Submit feedback


### Communication
- GitHub Issues for bug reports
- Discussions for questions
- Pull Requests for code changes


### Recognition
Contributors will be added to a `CONTRIBUTORS.md` file


## Need Help?
- Check existing issues and discussions
- Open a new discussion
- Contact maintainers


**Thank you for contributing! 🎉**