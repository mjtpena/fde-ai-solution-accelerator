FROM python:3.12-slim

WORKDIR /app
COPY workers/ingestion/pyproject.toml ./pyproject.toml
COPY workers/ingestion/src/ingestion_worker ./src/ingestion_worker
RUN python -m pip install --no-cache-dir .

USER 10001:10001

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os; os.kill(1, 0)"]

CMD ["python", "-m", "accelerator.ingestion"]
