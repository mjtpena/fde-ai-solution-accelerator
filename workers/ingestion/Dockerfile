# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f workers/ingestion/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.11.10@sha256:bca7f6959666f3524e0c42129f9d8bbcfb0c180d847f5187846b98ff06125ead AS uv

FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /workspace
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/agent_core/pyproject.toml packages/agent_core/pyproject.toml
COPY packages/retrieval_core/pyproject.toml packages/retrieval_core/pyproject.toml
COPY packages/evaluation_core/pyproject.toml packages/evaluation_core/pyproject.toml
COPY packages/observability_core/pyproject.toml packages/observability_core/pyproject.toml
COPY packages/security_core/pyproject.toml packages/security_core/pyproject.toml
COPY workers/ingestion/pyproject.toml workers/ingestion/pyproject.toml
RUN uv sync --frozen --no-dev --package fde-ingestion-worker --no-install-workspace

COPY packages ./packages
COPY workers/ingestion ./workers/ingestion
RUN uv sync --frozen --no-dev --package fde-ingestion-worker --no-editable

FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INGESTION_HEARTBEAT_FILE=/tmp/ingestion-heartbeat
RUN addgroup -g 10001 worker && adduser -u 10001 -G worker -D -H worker
COPY --from=builder /opt/venv /opt/venv
USER 10001:10001
# Fails when the worker's event loop stops refreshing its heartbeat file.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-m", "accelerator.ingestion.healthcheck"]
CMD ["python", "-m", "accelerator.ingestion"]
