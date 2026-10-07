.PHONY: setup up check eval-smoke

setup:
	uv sync --all-packages --frozen
	npm ci

up:
	docker compose up --build --detach

check:
	uv run --all-packages python -c "import ast; from pathlib import Path; roots = ('apps/api/src', 'packages/agent_core', 'packages/retrieval_core', 'packages/evaluation_core', 'packages/observability_core', 'packages/security_core', 'workers/ingestion/src'); [ast.parse(path.read_text(encoding='utf-8'), filename=str(path)) for root in roots for path in Path(root).rglob('*.py')]"
	uv run --all-packages python -m unittest discover -s tests -v
	npm run check --workspaces --if-present

eval-smoke:
	@echo "eval-smoke: not implemented until M5 (issue 30)"
