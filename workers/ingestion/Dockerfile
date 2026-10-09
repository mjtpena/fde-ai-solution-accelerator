FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4

WORKDIR /app
COPY workers/ingestion/pyproject.toml ./pyproject.toml
COPY workers/ingestion/src/ingestion_worker ./src/ingestion_worker
RUN python -m pip install --no-cache-dir .

USER 10001:10001

# Fails when the worker's event loop stops refreshing its heartbeat file.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-m", "accelerator.ingestion.healthcheck"]

CMD ["python", "-m", "accelerator.ingestion"]
