# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f apps/api/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a AS uv

FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS builder
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

FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN addgroup -g 10001 api && adduser -u 10001 -G api -D -H api
COPY --from=builder /opt/venv /opt/venv
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).close()"]
# The same image runs migrations: python -m accelerator.migrations upgrade head
CMD ["uvicorn", "--factory", "accelerator.api.main:create_application", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
