FROM python:3.12-slim

RUN python -m pip install --no-cache-dir \
    bandit==1.8.6 \
    pytest==8.4.2 \
    ruff==0.13.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

WORKDIR /workspace
