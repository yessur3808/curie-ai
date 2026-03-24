# Contributing to Curie AI 🚀

Thanks for taking the time to contribute! ❤️

## Code of Conduct
This project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

### Prerequisites
- Python 3.10+
- pip / virtualenv

### Setup

```bash
git clone https://github.com/yessur3808/curie-ai
cd curie-ai
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Development Process

```bash
git checkout -b feature/your-feature-name
# make changes
pytest
```

### Commit Format
```
type(scope): description
# e.g. feat: add OAuth2 authentication
```
Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

## Pull Request Process
1. Update documentation if needed
2. Add tests for new features
3. Ensure all tests pass (`make test`)
4. Request review from maintainers

## Style Guidelines

- **Python**: PEP 8, type hints, max 88 chars (Black), docstrings
- **Docs**: Clear, concise, with examples

## Community

- **Bugs / Features**: GitHub Issues
- **Questions**: GitHub Discussions
- **Code changes**: Pull Requests
- **Recognition**: Merged contributors added to [CONTRIBUTORS.md](CONTRIBUTORS.md)

**Thank you for contributing! 🎉**
