PYTHONPATH := $(shell pwd)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "%-16s %s\n", $$1, $$2}'

install: ## Install dependencies into a uv-managed venv
	uv sync

dev-install: ## Install dev dependencies too
	uv sync --all-groups

tool-install: ## Install `quelle` globally via `uv tool install` (re-runnable)
	uv tool install --force .

run: ## Run the quelle CLI (pass args via ARGS, e.g. make run ARGS="config")
	uv run quelle $(ARGS)

test: ## Run pytest
	uv run pytest

coverage: ## Run pytest with line-coverage report
	uv run pytest --cov=quelle --cov-report=term-missing --cov-report=html

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Ruff auto-fix + format
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: help install dev-install tool-install run test coverage lint format
