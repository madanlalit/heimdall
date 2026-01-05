# Heimdall - Command Runner
# Usage: just <recipe>

# Default recipe - show available commands
default:
    @just --list

# ─────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────

# Install dependencies (dev mode)
install:
    uv pip install -e ".[dev]"

# Sync dependencies from lockfile
sync:
    uv sync

# ─────────────────────────────────────────────────────────────
# Running the Agent
# ─────────────────────────────────────────────────────────────

# Run agent with a task and URL
run task url *args:
    uv run heimdall run "{{task}}" --url "{{url}}" {{args}}

# Run with specific model
run-model task url model:
    uv run heimdall run "{{task}}" --url "{{url}}" --model "{{model}}"

# ─────────────────────────────────────────────────────────────
# Code Quality
# ─────────────────────────────────────────────────────────────

# Lint code with ruff
lint:
    uv run ruff check src/

# Lint and fix auto-fixable issues
lint-fix:
    uv run ruff check src/ --fix

# Format code with ruff
fmt:
    uv run ruff format src/

# Check formatting without changes
fmt-check:
    uv run ruff format src/ --check

# Type check with ty
typecheck:
    uv run ty check

# Run all checks (lint + typecheck)
check: lint typecheck

# Fix and format everything
fix: lint-fix fmt

# ─────────────────────────────────────────────────────────────
# Testing
# ─────────────────────────────────────────────────────────────

# Run tests
test *args:
    uv run pytest {{args}}

# Run tests with verbose output
test-v:
    uv run pytest -v

# Run specific test file
test-file file:
    uv run pytest "{{file}}" -v

# ─────────────────────────────────────────────────────────────
# Examples
# ─────────────────────────────────────────────────────────────

# Run basic example
example:
    uv run python examples/test_basic.py

# ─────────────────────────────────────────────────────────────
# Development
# ─────────────────────────────────────────────────────────────

# Show heimdall CLI help
help:
    uv run heimdall --help

# Show version
version:
    uv run heimdall version

# Clean cache files
clean:
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".heimdall" -exec rm -rf {} + 2>/dev/null || true