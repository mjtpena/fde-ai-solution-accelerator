"""Redaction helpers for sensitive values that may enter telemetry."""

from collections.abc import Mapping, Sequence
import re
from typing import TYPE_CHECKING, Callable, overload

if TYPE_CHECKING:
    from opentelemetry.util.types import AttributeValue as OpenTelemetryAttributeValue

_REDACTED = "[" + "REDACTED" + "]"
_REDACTED_EMAIL = "[REDACTED_EMAIL]"

type AttributeValue = (
    str | bool | int | float | Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]
)
type RedactionValue = (
    str
    | bool
    | int
    | float
    | bytes
    | Sequence[RedactionValue]
    | Mapping[str, RedactionValue]
    | None
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>\b(?:key|account[_-]?key|api[_-]?key|access[_-]?key|"
    r"refresh[_-]?key|client[_-]?secret|private[_-]?key|secret|password|"
    r"passwd|auth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|token|credential|credentials)\b[\"']?\s*[:=]\s*)"
    r"(?P<value>(?:[Bb]earer\s+[^\s,;}\]]+|\"[^\"\r\n]*\"|'[^'\r\n]*'|"
    r"[^\s,;}\]]+))"
)
_AUTHORIZATION_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>\bauthorization\b[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|"
    r"[A-Za-z][A-Za-z0-9_-]*\s+[^\s,;}\]]+|[^\s,;}\]]+)"
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
_SENSITIVE_KEY_PARTS = {
    "authorization",
    "credential",
    "credentials",
    "key",
    "password",
    "passwd",
    "secret",
    "token",
}


@overload
def redact_sensitive_data(value: str) -> str: ...


@overload
def redact_sensitive_data(value: bytes) -> bytes: ...


def redact_sensitive_data(value: str | bytes) -> str | bytes:
    """Redact credential assignments, tokens, and emails in text or UTF-8 bytes."""
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return _REDACTED.encode("ascii")
        redacted = _redact_text(text)
        return value if redacted == text else redacted.encode("utf-8")
    return _redact_text(value)


def _redact_text(value: str) -> str:
    redacted = _AUTHORIZATION_ASSIGNMENT.sub(_redact_assignment, value)
    redacted = _SENSITIVE_ASSIGNMENT.sub(_redact_assignment, redacted)
    redacted = _BEARER_TOKEN.sub(lambda match: "Bearer " + _REDACTED, redacted)
    redacted = _JWT.sub(_REDACTED, redacted)
    redacted = _KNOWN_TOKEN.sub(_REDACTED, redacted)
    return _EMAIL.sub(_REDACTED_EMAIL, redacted)


def _redact_assignment(match: re.Match[str]) -> str:
    value = match["value"]
    if value.startswith(("'", '"')):
        return f"{match['prefix']}{value[0]}{_REDACTED}{value[0]}"
    return f"{match['prefix']}{_REDACTED}"


@overload
def redact_attributes(attributes: Mapping[str, AttributeValue]) -> dict[str, AttributeValue]: ...


@overload
def redact_attributes(attributes: Mapping[str, RedactionValue]) -> dict[str, RedactionValue]: ...


def redact_attributes(
    attributes: Mapping[str, RedactionValue],
) -> Mapping[str, RedactionValue]:
    """Return copied attributes with sensitive values redacted recursively."""
    return {key: _redact_attribute_value(key, value) for key, value in attributes.items()}


def _redact_attribute_value(key: str, value: RedactionValue) -> RedactionValue:
    if _is_sensitive_key(key):
        return _replacement_for(value)
    if isinstance(value, (str, bytes)):
        return redact_sensitive_data(value)
    if isinstance(value, Mapping):
        return redact_attributes(value)
    if isinstance(value, Sequence):
        return tuple(_redact_attribute_value(key, item) for item in value)
    return value


def _replacement_for(value: RedactionValue) -> str | bytes:
    return _REDACTED.encode("ascii") if isinstance(value, bytes) else _REDACTED


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])", "_", key)
    components = re.split(r"[^A-Za-z0-9]+", normalized.casefold())
    return bool(
        components and (components[-1] in _SENSITIVE_KEY_PARTS or "authorization" in components)
    )


if TYPE_CHECKING:
    _sanitizer_type_check: Callable[
        [Mapping[str, OpenTelemetryAttributeValue]],
        Mapping[str, OpenTelemetryAttributeValue],
    ] = redact_attributes


__all__ = ["AttributeValue", "RedactionValue", "redact_attributes", "redact_sensitive_data"]
