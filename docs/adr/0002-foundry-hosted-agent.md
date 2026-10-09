# ADR-0002: Package the grounded workflow as a Foundry hosted agent alongside the API

**Status:** Accepted
**Date:** 2026-10-09

## Context

Spec §2 selects Microsoft Foundry Agent Service (hosted agent) as the agent runtime.
Spec §3 also says the API must never run long-lived agent loops. Two kinds of
consumer need the grounded-answer workflow (spec §6.2):

1. **The product's own web app.** It needs Entra sign-in, server-side scope
   resolution, streaming tokens, per-turn tool offers, approval cards, audit and
   feedback. All of this lives in the FastAPI API and its PostgreSQL state.
2. **Foundry-native consumers.** These are other agents, Foundry playgrounds, or
   client systems that call a Foundry agent endpoint. They expect a managed runtime
   with Foundry identity, RBAC and versioning.

The grounded workflow is bounded. It makes one retrieval, one sufficiency check, at
most one generation call, and one citation validation. It is not an open-ended agent
loop. Writing a second implementation for the hosted runtime would let the two
drift apart in scope enforcement, abstention and citation checks.

## Decision

Keep **one** workflow implementation, `GroundedAnswerWorkflow`, and expose it in two
ways:

- **In the API (default product path).** `build_azure_grounded_answer`
  (`apps/api/src/accelerator/infrastructure/grounded_answer.py`) composes the
  workflow in-process. It uses the Azure AI Search retriever, the sufficiency
  checker, a tool-less Foundry agent as generator, and same-turn citation validation.
  The workflow runs inside the API container in Azure Container Apps (ADR-0006). Model
  inference still runs on Foundry model deployments through `FoundryChatClient`. Only
  the bounded orchestration runs in the API. Chat, streaming, tool turns
  (`apps/api/src/accelerator/api/tool_turns.py`) and approvals use this path.
- **As a Foundry hosted agent (Invocations protocol).** `infrastructure/hosted_agent/`
  packages the same workflow in a container image (`Dockerfile`, linux/amd64,
  port 8088, UID 10001).
  - `server.py` uses `InvocationAgentServerHost` from
    `azure-ai-agentserver-invocations`. It accepts only `{"query": "..."}` up to
    64 KiB and forwards only the `Authorization` header to the application.
  - `WorkflowHostedApplication` (`packages/agent_core/hosting/application.py`)
    resolves the `ExecutionContext` through a `ContextResolver`, runs the workflow,
    and returns an `InvocationResult`. `contracts.py` validates that result: an
    answer must have text and citations, and an abstention must have a reason only.
  - `production.py` builds the application from two trusted settings,
    `HOSTED_CONTEXT_RESOLVER_FACTORY` and `HOSTED_GROUNDED_WORKFLOW_FACTORY`. If
    either is missing or invalid, startup fails. There is no echo or raw-model
    fallback.
  - `service.py` and `cli.py` create a hosted-agent version, poll until it is
    `active`, and smoke-invoke the agent endpoint.

Use the hosted agent when the consumer is Foundry-native and needs only a read-only
grounded answer or an abstention. Use the API path when a person uses the product UI,
or when tools, approvals, sessions, streaming or audit are involved.

## Consequences

Positive:

- The control plane is the same on both paths. Scope comes from server-side
  resolution, retrieval filters are injected, abstention and citation validation are
  executors, and the hosted path does not bypass them.
- The hosted agent gets the Foundry-managed runtime, a dedicated Entra identity,
  versioning and a Foundry endpoint without a separate code base.
- `.github/workflows/deploy-dev.yml` builds the agent image with the other images. It
  deploys and smoke-tests the agent in dev, and again when production is promoted.

Negative / trade-offs:

- Two hosting surfaces to operate, secure and monitor.
- The accelerator does **not** ship a production `ContextResolver` or workflow factory
  for the hosted agent. Each project must supply both and package them in the image.
  It must also confirm end to end that the Foundry gateway forwards a credential it
  can verify (see `infrastructure/hosted_agent/README.md`). Until those exist, the
  deploy step fails on purpose. It refuses to run without
  `HOSTED_APPLICATION_FACTORY`, and `DeploymentSettings`
  (`infrastructure/hosted_agent/configuration.py`) also requires the two composition
  factory settings.
- The hosted path does not support tool calls, approvals, streaming or sessions, so
  the two surfaces do not offer the same features.
- Hosted-agent resources are not provisioned in `infrastructure/main.bicep`. The
  project, region support and ACR pull rights for the project identity are external
  prerequisites.
- Spec §2 describes the hosted agent as *the* agent runtime. In practice, the
  product's interactive path runs the workflow in the API container.

## Alternatives considered

- **Hosted agent only.** The UI would lose streaming, per-turn tool policy, approval
  cards and audit in PostgreSQL. All of these depend on API-owned state.
- **API only.** Foundry-native consumers would have no managed endpoint, and the
  spec's runtime choice would not be exercised at all.
- **A Foundry framework-hosted Responses agent exposing the raw model agent.** This
  would bypass evidence sufficiency and citation validation. The README records that
  framework hosting is optional and that this transport hosts the explicit workflow
  instead.
- **A self-hosted agent loop in the API.** Ruled out by spec §2 and §3. The API runs
  only the bounded workflow.

## References

- `docs/spec.md` §2, §3 (responsibility boundaries), §6.2
- `infrastructure/hosted_agent/README.md`, `server.py`, `production.py`, `service.py`,
  `Dockerfile`, `deployment.example.json`
- `packages/agent_core/hosting/application.py`, `packages/agent_core/hosting/contracts.py`
- `apps/api/src/accelerator/infrastructure/grounded_answer.py`
- `apps/api/src/accelerator/api/composition.py`, `apps/api/src/accelerator/api/tool_turns.py`
- `.github/workflows/deploy-dev.yml` (hosted agent deploy and smoke steps)
- https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agent-contract
- https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent
- https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/framework-hosted-agents
