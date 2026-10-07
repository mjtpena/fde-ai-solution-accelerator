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
