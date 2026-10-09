.PHONY: setup up check migrate eval-smoke eval-full

setup:
	uv sync --all-packages --frozen
	npm ci
	uv run pre-commit install

up:
	docker compose up --build --detach

# Apply database migrations. Reads API_DATABASE_URL (and API_DATABASE_AUTH_MODE).
migrate:
	uv run --all-packages python -m accelerator.migrations upgrade head

check:
	uv run --all-packages ruff check
	uv run --all-packages mypy --strict
	uv run --all-packages mypy --strict --package accelerator.retrieval_core
	uv run --all-packages pytest
	npm run check --workspaces --if-present

# A project-owned evaluations/thresholds.yml takes precedence over the shipped example.
EVAL_THRESHOLDS ?= $(firstword $(wildcard evaluations/thresholds.yml) evaluations/thresholds.example.yml)

eval-smoke:
	uv run --all-packages python -m accelerator.evaluation_core.reporting.smoke \
		--baseline evaluations/baselines/accepted.json --thresholds $(EVAL_THRESHOLDS)

eval-full:
	uv run --all-packages python -m accelerator.evaluation_core.evaluators

# BEGIN PROJECT GENERATOR
# Usage: make new-project NAME=my-solution DISPLAY="My Solution" [DEST=absolute-path]
# Fixtures and typing run in CI via make check; generated projects omit this block.
.PHONY: new-project check-generator
export NAME DISPLAY DEST

check: check-generator

check-generator:
	uv run --all-packages mypy --strict scripts/new_project.py
	uv run --all-packages python scripts/new_project.py --self-test
	uv run --all-packages python scripts/new_project.py --check-generated

new-project:
	uv run --all-packages python scripts/new_project.py
# END PROJECT GENERATOR
