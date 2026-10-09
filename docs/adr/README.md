# Architecture Decision Records

Each record explains one decision behind the accelerator: the context, what was
decided, the consequences, and the alternatives that were rejected. The technology
summary is in `docs/spec.md` §2.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-microsoft-agent-framework.md) | Microsoft Agent Framework as the only agent SDK | Accepted |
| [0002](0002-foundry-hosted-agent.md) | Package the grounded workflow as a Foundry hosted agent alongside the API | Accepted |
| [0003](0003-direct-ai-search.md) | Call Azure AI Search directly with a context-injected scope filter | Accepted |
| [0004](0004-approval-bound-to-arguments.md) | Bind approvals to the tool, canonical arguments, scope, requester and expiry | Accepted |
| [0005](0005-foundry-evaluation.md) | Deterministic smoke gate on every PR, Foundry evaluators for full evaluation | Accepted |
| [0006](0006-container-apps.md) | Azure Container Apps for the API, web, worker and migration job | Accepted |

## Adding an ADR

- Copy the structure of an existing record: Status, Date, Context, Decision,
  Consequences (positive and negative), Alternatives considered, References.
- Use the next number.
- Ground every claim in repository paths. If something is not implemented yet,
  say so.
- Do not edit an accepted ADR to reverse it. Write a new ADR, and set the old one's
  status to `Superseded by ADR-NNNN`.
