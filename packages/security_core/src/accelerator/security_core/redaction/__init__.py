"""Redaction helpers for sensitive values that may enter telemetry."""

from collections.abc import Mapping, Sequence
import re

_REDACTED = "[REDACTED]"
_REDACTED_EMAIL = "[REDACTED_EMAIL]"

type AttributeValue = (
    str
    | bool
    | int
    | float
    | bytes
    | Sequence[AttributeValue]
    | Mapping[str, AttributeValue]
    | None
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>\b(?:key|account[_-]?key|api[_-]?key|access[_-]?key|"
    r"refresh[_-]?key|client[_-]?secret|private[_-]?key|secret|password|"
    r"passwd|authorization|auth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|token|credential|credentials)\b[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;}\]]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,})\b"
)
_EMAIL = re.compile(
    r"\b[A-Za-z0-9.!#$%&'*+/?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
    r"[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\b"
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[^a-z0-9])"
    r"(?:key|token|secret|password|passwd|credential|credentials|authorization)$"
)


def redact_sensitive_data(value: str) -> str:
    """Redact credential assignments, bearer/JWT/provider tokens, and emails."""
    redacted = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match['prefix']}{_REDACTED}",
        value,
    )
    redacted = _BEARER_TOKEN.sub(lambda match: "Bearer " + _REDACTED, redacted)
    redacted = _JWT.sub(_REDACTED, redacted)
    redacted = _KNOWN_TOKEN.sub(_REDACTED, redacted)
    return _EMAIL.sub(_REDACTED_EMAIL, redacted)


def redact_attributes(attributes: Mapping[str, AttributeValue]) -> dict[str, AttributeValue]:
    """Return copied span attributes with sensitive values redacted recursively."""
    return {key: _redact_attribute_value(key, value) for key, value in attributes.items()}


def _redact_attribute_value(key: str, value: AttributeValue) -> AttributeValue:
    if _SENSITIVE_KEY.search(key):
        return _REDACTED
    if isinstance(value, str):
        return redact_sensitive_data(value)
    if isinstance(value, Mapping):
        return redact_attributes(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(_redact_attribute_value(key, item) for item in value)
    return value


__all__ = ["AttributeValue", "redact_attributes", "redact_sensitive_data"]