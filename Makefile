.DEFAULT_GOAL := help

UV ?= uv
PYTEST_ARGS ?=
TEST_DATABASE ?= postgres
PYTHON_PATHS := src tests scripts docs/conf.py

.PHONY: help setup sync check lint format format-check traceability typecheck \
        test test-unit test-postgres test-e2e test-compatibility build docs audit clean hooks

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target> [UV=uv] [PYTEST_ARGS=\"...\"]\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: sync hooks ## Install locked development dependencies and Git hooks

hooks: ## Install Conventional Commit and pre-push quality hooks
	$(UV) run --locked pre-commit install

sync: ## Synchronize dependencies from uv.lock
	$(UV) sync --locked --all-groups

check: format-check traceability typecheck ## Check formatting, lint, type annotations, and requirement assignments

lint: ## Lint Python code with Ruff
	$(UV) run ruff check $(PYTHON_PATHS)

format: ## Apply lint fixes and format Python code
	$(UV) run ruff check --fix $(PYTHON_PATHS)
	$(UV) run ruff format $(PYTHON_PATHS)

format-check: ## Check Python formatting and lint without changing files
	$(UV) run ruff format --check $(PYTHON_PATHS)
	$(UV) run ruff check $(PYTHON_PATHS)

traceability: ## Validate the requirement verification matrix
	$(UV) run python scripts/check_traceability.py

typecheck: ## Type-check the library with mypy and django-stubs
	$(UV) run mypy

test: test-unit ## Run the fast default suite without external services

test-unit: ## Run library unit tests without probing PostgreSQL
	$(UV) run --locked pytest tests/unit $(PYTEST_ARGS)

test-postgres: ## Run all PostgreSQL integration tests; fail if unavailable
	REQUIRE_POSTGRES=1 $(UV) run --locked pytest tests/integration $(PYTEST_ARGS)

test-e2e: test-postgres ## Run PostgreSQL integration and the consumer browser journey
	$(UV) run --directory ../django-aws-tasks-example --locked pytest sample_app/tests tests --ignore=tests/aws -m 'not browser' $(PYTEST_ARGS)
	RUN_BROWSER_TESTS=1 $(UV) run --directory ../django-aws-tasks-example --locked --group browser pytest sample_app/tests -m browser $(PYTEST_ARGS)

test-compatibility: ## Run shared contracts on TEST_DATABASE (default: postgres)
	TEST_DATABASE=$(TEST_DATABASE) $(UV) run --locked --extra $(TEST_DATABASE) pytest tests/compatibility --ds=tests.compatibility_settings $(PYTEST_ARGS)

build: ## Build wheel and source distributions into dist/
	$(UV) build

docs: ## Build Sphinx HTML docs; fail on warnings
	$(UV) run --group docs sphinx-build -b html docs docs/_build/html -W --keep-going

audit: ## Audit locked library and development dependencies for known vulnerabilities
	mkdir -p artifacts
	$(UV) export --locked --all-packages --all-groups --no-emit-workspace --format requirements-txt --output-file artifacts/audit-requirements.txt > /dev/null
	$(UV) run --locked pip-audit --require-hashes --disable-pip -r artifacts/audit-requirements.txt

clean: ## Remove project build and cache artifacts; preserve the virtual environment
	rm -rf build dist docs/_build .pytest_cache .ruff_cache
	find src tests scripts docs -type d \( -name .venv -o -name .terraform \) -prune -o -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
