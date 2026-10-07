---
applyTo: "agents/**,packages/agent_core/**"
---
# Agent rules (Microsoft Agent Framework)
- Use Microsoft Agent Framework primitives only (agents, workflows, tools, middleware). Verify APIs on Microsoft Learn; the SDK changes often.
- Prefer explicit workflows. Use model-driven tool selection only where the model must choose between legitimate actions.
- Every tool subclasses `EnterpriseTool` with a declared `ToolRisk` and a Pydantic args model.
- Tools never take scope/project IDs as arguments; read them from `ExecutionContext`.
- Write-risk tools return `ApprovalRequired`; policy middleware enforces this, not the prompt.
- Instructions live in Markdown files under `instructions/`, not inline strings.
- Wrap retrieved text as untrusted data in prompts. Never concatenate it into system instructions.
- Every new tool needs: unit tests, a tool-selection eval row, and a policy test.
