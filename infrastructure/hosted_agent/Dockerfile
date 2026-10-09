FROM ghcr.io/astral-sh/uv:0.11.10@sha256:bca7f6959666f3524e0c42129f9d8bbcfb0c180d847f5187846b98ff06125ead AS uv
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
COPY packages /app/packages
COPY apps/api /app/apps/api
COPY infrastructure/hosted_agent/pyproject.toml infrastructure/hosted_agent/uv.lock /app/infrastructure/hosted_agent/
RUN uv sync --project infrastructure/hosted_agent --frozen --no-dev --no-editable

FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /opt/venv /opt/venv
COPY infrastructure/__init__.py /app/infrastructure/__init__.py
COPY infrastructure/hosted_agent/*.py /app/infrastructure/hosted_agent/
RUN addgroup -g 10001 agent && adduser -u 10001 -G agent -D agent
USER 10001:10001
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/readiness', timeout=4).close()"]
CMD ["python", "-m", "infrastructure.hosted_agent.server"]
