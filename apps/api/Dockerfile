# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f apps/api/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.11.10@sha256:bca7f6959666f3524e0c42129f9d8bbcfb0c180d847f5187846b98ff06125ead AS uv

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /workspace
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# Third-party dependencies first, from the lockfile only, so source edits reuse this layer.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/agent_core/pyproject.toml packages/agent_core/pyproject.toml
COPY packages/retrieval_core/pyproject.toml packages/retrieval_core/pyproject.toml
COPY packages/evaluation_core/pyproject.toml packages/evaluation_core/pyproject.toml
COPY packages/observability_core/pyproject.toml packages/observability_core/pyproject.toml
COPY packages/security_core/pyproject.toml packages/security_core/pyproject.toml
COPY workers/ingestion/pyproject.toml workers/ingestion/pyproject.toml
RUN uv sync --frozen --no-dev --package fde-accelerator-api --no-install-workspace

# Then the workspace packages the API needs, installed as regular (non-editable) wheels.
COPY packages ./packages
COPY apps/api ./apps/api
RUN uv sync --frozen --no-dev --package fde-accelerator-api --no-editable

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 api && useradd --uid 10001 --gid api --no-create-home api
COPY --from=builder /opt/venv /opt/venv
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).close()"]
# The same image runs migrations: python -m accelerator.migrations upgrade head
CMD ["uvicorn", "--factory", "accelerator.api.main:create_application", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
