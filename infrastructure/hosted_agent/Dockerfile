FROM ghcr.io/astral-sh/uv:0.11.10 AS uv
FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
COPY packages /app/packages
COPY apps/api /app/apps/api
COPY infrastructure/hosted_agent/pyproject.toml infrastructure/hosted_agent/uv.lock /app/infrastructure/hosted_agent/
RUN uv sync --project infrastructure/hosted_agent --frozen --no-dev --no-editable

FROM python:3.12-slim
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /opt/venv /opt/venv
COPY infrastructure/__init__.py /app/infrastructure/__init__.py
COPY infrastructure/hosted_agent/*.py /app/infrastructure/hosted_agent/
RUN groupadd --gid 10001 agent && useradd --uid 10001 --gid agent --create-home agent
USER 10001:10001
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/readiness', timeout=4).close()"]
CMD ["python", "-m", "infrastructure.hosted_agent.server"]
