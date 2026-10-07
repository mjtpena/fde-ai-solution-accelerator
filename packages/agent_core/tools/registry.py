"""Discovery only: registering a tool never executes it or grants approval."""

import math
from typing import Any

from pydantic import AliasChoices, AliasPath, BaseModel

from .base import EnterpriseTool, ToolRisk

_RESERVED_ARGUMENT_FIELDS = frozenset(
    {"scope_id", "scope_ids", "project_id", "project_ids"}
)


def _alias_contains_reserved_field(alias: object) -> bool:
    if isinstance(alias, str):
        return alias in _RESERVED_ARGUMENT_FIELDS
    if isinstance(alias, AliasChoices):
        return any(_alias_contains_reserved_field(choice) for choice in alias.choices)
    if isinstance(alias, AliasPath):
        return any(_alias_contains_reserved_field(part) for part in alias.path)
    return False


def _schema_contains_reserved_field(schema: object) -> bool:
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict) and _RESERVED_ARGUMENT_FIELDS.intersection(
            properties
        ):
            return True
        return any(_schema_contains_reserved_field(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_schema_contains_reserved_field(value) for value in schema)
    return False


class ToolRegistry:
    """Keep uniquely named, risk-classified tools for policy-mediated use.

    Argument/result generics are erased in this heterogeneous collection. Each
    concrete tool retains its typed execute contract and Pydantic args model.
    """

    def __init__(self) -> None:
        self._tools: dict[str, EnterpriseTool[Any, Any]] = {}

    def register(self, tool: EnterpriseTool[Any, Any]) -> None:
        if not isinstance(tool, EnterpriseTool):
            raise TypeError("Only EnterpriseTool instances can be registered")
        if not isinstance(getattr(tool, "risk", None), ToolRisk):
            raise ValueError("Tools must declare a ToolRisk")
        if tool.risk is ToolRisk.PROHIBITED:
            raise ValueError("PROHIBITED tools cannot be registered")
        if not isinstance(getattr(tool, "name", None), str) or not tool.name.strip():
            raise ValueError("Tools must declare a non-empty name")
        if (
            not isinstance(getattr(tool, "description", None), str)
            or not tool.description.strip()
        ):
            raise ValueError("Tools must declare a non-empty description")
        args_model = getattr(tool, "args_model", None)
        if not isinstance(args_model, type) or not issubclass(args_model, BaseModel):
            raise ValueError("Tools must declare a Pydantic args model")
        if args_model.model_config.get("extra") == "allow":
            raise ValueError("Tool args models cannot allow undeclared extra fields")
        if any(
            field_name in _RESERVED_ARGUMENT_FIELDS
            or _alias_contains_reserved_field(field.alias)
            or _alias_contains_reserved_field(field.validation_alias)
            or _alias_contains_reserved_field(field.serialization_alias)
            for field_name, field in args_model.model_fields.items()
        ) or _schema_contains_reserved_field(
            args_model.model_json_schema(mode="validation", by_alias=True)
        ):
            raise ValueError("Scope and project IDs must come from ExecutionContext")
        if (
            isinstance(tool.timeout_seconds, bool)
            or not isinstance(tool.timeout_seconds, (int, float))
            or not math.isfinite(tool.timeout_seconds)
            or tool.timeout_seconds <= 0
        ):
            raise ValueError("Tool timeout must be finite and positive")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> EnterpriseTool[Any, Any]:
        """Look up a tool; unknown names raise KeyError."""
        return self._tools[name]

    def list_tools(self) -> tuple[EnterpriseTool[Any, Any], ...]:
        """Return a snapshot in registration order, never a mutable registry."""
        return tuple(self._tools.values())
