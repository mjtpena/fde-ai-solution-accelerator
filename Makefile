.PHONY: setup up check eval-smoke

setup:
	uv sync --all-packages --frozen
	npm ci
	uv run pre-commit install

up:
	docker compose up --build --detach

check:
	uv run --all-packages ruff check
	uv run --all-packages mypy --strict
	uv run --all-packages pytest
	npm run check --workspaces --if-present

eval-smoke:
	@echo "eval-smoke: not implemented until M5 (issue 30)"

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
