SHELL := /bin/bash

.PHONY: run start test migrate migrate-down lint format shell

run:
	python main.py

start: run

test:  ## Run all tests
	pytest tests/

lint:  ## Lint code with flake8
	flake8 src/ tests/

format:  ## Format code with black
	black src/ tests/

migrate:  ## Apply all SQL up migrations
	set -a && source .env && set +a && python scripts/apply_migrations.py

migrate-down:  ## Revert all migrations (dangerous!)
	set -a && source .env && set +a && python scripts/down_migrations.py

shell:  ## Open a Python shell with project imported
	python -i main.py

help:  ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

	