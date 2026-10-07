---
mode: agent
description: Add a new agent tool with policy, tests and eval rows
---
Add an agent tool named `${input:name:tool_name}` that ${input:purpose:does what}.

- Subclass `EnterpriseTool`; declare `ToolRisk` (${input:risk:read_only|low_impact_write|high_impact_write}).
- Define a Pydantic args model. No scope/project ID argument.
- Register it in the tool registry.
- Tests: success, validation failure, timeout, policy (write → ApprovalRequired).
- Add at least 3 rows to `evaluations/datasets/tool-selection.jsonl`.
- Run `make check` and `make eval-smoke`.
