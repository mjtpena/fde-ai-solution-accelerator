from __future__ import annotations

from typing import Literal

from .models import ToolPolicyViolation


ToolPolicyAction = Literal["execute", "approval"]


class ToolPolicy:
    def evaluate(self, risk_value: str) -> ToolPolicyAction:
        if risk_value == "prohibited":
            raise ToolPolicyViolation("prohibited_tool")
        if risk_value == "read_only":
            return "execute"
        if risk_value in {"low_impact_write", "high_impact_write", "privileged"}:
            return "approval"
        raise ToolPolicyViolation("unknown_tool_risk")
