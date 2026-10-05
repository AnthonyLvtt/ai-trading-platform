.PHONY: setup format format-check lint typecheck test validate diagnostic

setup:
	uv sync --dev

format:
	uv run ruff format src tests

format-check:
	uv run ruff format --check src tests

lint:
	uv run ruff check src tests

typecheck:
	uv run mypy src

test:
	uv run pytest

validate: format-check lint typecheck test

diagnostic:
	ATP_ENV=LOCAL uv run atp-diagnostic
