SHELL := /bin/bash

.PHONY: install install-optional run start test migrate migrate-down lint format shell verify help
.PHONY: db-start db-stop db-restart db-status setup-db
.PHONY: run-telegram run-discord run-whatsapp run-api run-all
.PHONY: check-ports test-imports clean sync-env sync-env-add sync-env-clean sync-env-backup restart-clean

# Installation targets
install:  ## Install all dependencies from requirements.txt
	pip install -r requirements.txt

install-optional:  ## Install optional dependencies (voice features, Discord, WhatsApp)
	pip install -r requirements-optional.txt

verify:  ## Verify setup and dependencies
	python scripts/verify_setup.py

# Database management
db-start:  ## Start PostgreSQL and MongoDB with Docker
	docker-compose up -d postgres mongo
	@echo "Waiting for databases to start..."
	@sleep 5
	@echo "Databases started!"

db-stop:  ## Stop database services
	docker-compose down

db-restart:  ## Restart database services
	docker-compose restart postgres mongo

db-status:  ## Check database service status
	docker-compose ps

setup-db:  ## Setup databases and run migrations (run after db-start)
	python scripts/apply_migrations.py
	python scripts/gen_master_id.py
	python scripts/insert_master.py
	@echo "Database setup complete!"

# Application runners
run:  ## Run main application (default connectors from .env)
	python main.py

start: run  ## Alias for 'run'

run-telegram:  ## Run with Telegram connector only
	python main.py --telegram

run-discord:  ## Run with Discord connector only
	python main.py --discord

run-whatsapp:  ## Run with WhatsApp connector only
	python main.py --whatsapp

run-api:  ## Run with API server only
	python main.py --api

run-all:  ## Run with all connectors enabled
	python main.py --all

# Development tools
test:  ## Run all tests
	pytest tests/

lint:  ## Lint code with flake8
	flake8 agent/ connectors/ llm/ memory/ services/ utils/ scripts/ main.py tests/

format:  ## Format code with black
	black agent/ connectors/ llm/ memory/ services/ utils/ scripts/ main.py tests/

check-ports:  ## Check if required ports are available
	python scripts/check_ports.py

test-imports:  ## Test that all imports work correctly
	python scripts/test_import.py

shell:  ## Open a Python shell with project imported
	python -i main.py

# Migration management
migrate:  ## Apply all SQL up migrations
	set -a && source .env && set +a && python scripts/apply_migrations.py

migrate-down:  ## Revert all migrations (dangerous!)
	set -a && source .env && set +a && python scripts/down_migrations.py

# Cleanup
clean:  ## Remove Python cache files and logs
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.log" -delete
	@echo "Cache and log files cleaned!"

restart-clean:  ## Clean cache/logs, restart databases, and restart application
	@echo "Cleaning cache and logs..."
	@$(MAKE) clean
	@echo "Restarting databases..."
	@$(MAKE) db-restart
	@echo "Starting application..."
	@$(MAKE) run

# Environment management
sync-env:  ## Sync .env file with .env.example (check differences)
	python scripts/sync_env.py

sync-env-add:  ## Add missing variables from .env.example to .env
	python scripts/sync_env.py --sync

sync-env-clean:  ## Interactively remove obsolete variables from .env
	python scripts/sync_env.py --clean

sync-env-backup:  ## Sync .env with backup
	python scripts/sync_env.py --sync --backup

# Help
help:  ## Show available commands
	@echo "Available make targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Common workflows:"
	@echo "  1. First-time setup:"
	@echo "     make install && make db-start && make setup-db"
	@echo ""
	@echo "  2. Start application:"
	@echo "     make run-telegram  (or run-discord, run-api, run-all)"
	@echo ""
	@echo "  3. Verify installation:"
	@echo "     make verify"
	@echo ""
	@echo "  4. Sync .env file:"
	@echo "     make sync-env          # Check differences"
	@echo "     make sync-env-add      # Add missing variables"
	@echo "     make sync-env-clean    # Remove obsolete variables"
	@echo ""
	@echo "  5. Clean restart:"
	@echo "     make restart-clean     # Clean, restart DB, and run"
	@echo ""

	