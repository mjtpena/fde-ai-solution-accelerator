.PHONY: setup up check migrate openapi openapi-check eval-smoke eval-full deploy-dev

setup:
	uv sync --all-packages --frozen
	npm ci
	uv run pre-commit install

up:
	docker compose up --build --detach

# Apply database migrations. Reads API_DATABASE_URL (and API_DATABASE_AUTH_MODE).
migrate:
	uv run --all-packages python -m accelerator.migrations upgrade head

# Test modules outside the workspace packages (the default run checks the packages).
MYPY_TEST_FILES = workers/ingestion/tests apps/api/tests/test_scope_resolver.py

# Extra pytest arguments, e.g. PYTEST_ARGS="--cov" to enforce [tool.coverage] in CI.
PYTEST_ARGS ?=

check:
	uv run --all-packages ruff check
	uv run --all-packages mypy --strict
	uv run --all-packages mypy --strict $(MYPY_TEST_FILES)
	uv run --all-packages pytest $(PYTEST_ARGS)
	npm run check --workspaces --if-present

# Regenerate the API contract and the web client types from the FastAPI app.
openapi:
	uv run --all-packages python apps/api/scripts/generate_openapi.py
	npm run generate:api --workspace apps/web

# Fails when the committed contract or client types differ from the code.
openapi-check: openapi
	git diff --exit-code -- contracts/api/openapi.json apps/web/lib/api/schema.d.ts

# A project-owned evaluations/thresholds.yml takes precedence over the shipped example.
EVAL_THRESHOLDS ?= $(firstword $(wildcard evaluations/thresholds.yml) evaluations/thresholds.example.yml)

eval-smoke:
	uv run --all-packages python -m accelerator.evaluation_core.reporting.smoke \
		--baseline evaluations/baselines/accepted.json --thresholds $(EVAL_THRESHOLDS)

# Full evaluation composes the API's Azure workflow; override either to evaluate
# a project's own dataset or composition.
EVALUATION_WORKFLOW_FACTORY ?= accelerator.infrastructure.evaluation:create_full_evaluation_runtime
EVALUATION_DATASET ?= evaluations/example-datasets/smoke.jsonl
export EVALUATION_WORKFLOW_FACTORY EVALUATION_DATASET

eval-full:
	uv run --all-packages python -m accelerator.evaluation_core.evaluators

# Deploy the dev environment with the signed-in Azure CLI identity: infrastructure,
# images (ACR Tasks), applications, migrations, search index, smoke test. One stage:
# make deploy-dev STAGE=smoke. See infrastructure/README.md for the variables and the
# one-time database bootstrap, which needs private network access.
STAGE ?= all

deploy-dev:
	infrastructure/scripts/deploy-dev.sh $(STAGE)

# BEGIN PROJECT GENERATOR
# Usage: make new-project NAME=my-solution DISPLAY="My Solution" [DEST=absolute-path]
# Fixtures and typing run in CI via make check; generated projects omit this block.
.PHONY: new-project check-generator
export NAME DISPLAY DEST

# CI runs the generated-project check as its own job (SKIP_GENERATOR=1 elsewhere).
ifeq ($(SKIP_GENERATOR),)
check: check-generator
endif

check-generator:
	uv run --all-packages mypy --strict scripts/new_project.py
	uv run --all-packages python scripts/new_project.py --self-test
	uv run --all-packages python scripts/new_project.py --check-generated

new-project:
	uv run --all-packages python scripts/new_project.py
# END PROJECT GENERATOR
