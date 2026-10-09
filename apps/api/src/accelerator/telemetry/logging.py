"""Structured JSON logs that keep the ``extra`` fields callers attach.

Every module logs an event name as the message and context (``correlation_id``,
``exception_type``, ...) as ``extra``. The default formatter drops those fields;
this one emits them as JSON keys. String values pass through ``security_core``
redaction, and exception messages and stack traces are never written because
they can contain prompts, retrieved text or credentials.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from accelerator.security_core.redaction import redact_sensitive_data

_STANDARD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_data(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return redact_sensitive_data(str(value))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact_sensitive_data(record.getMessage()),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
                entry[key] = _json_safe(value)
        if record.exc_info and record.exc_info[0] is not None:
            entry.setdefault("exception_type", record.exc_info[0].__name__)
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Route the root logger (and uvicorn's) through one JSON handler on stderr."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers[:] = []
        uvicorn_logger.propagate = True
