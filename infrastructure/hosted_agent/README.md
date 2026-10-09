# Foundry hosted-agent packaging

Issue #25 packages an **Invocations** agent, not a second agent loop or an Azure
Container App. The official protocol adapter provides port 8088, `/readiness`,
`/invocations` and graceful shutdown. Azure SDK calls are confined to
`azure_adapter.py` behind `HostedAgentGateway`. Resource provisioning belongs to #32.

## Required application composition

The packaged `infrastructure.hosted_agent.production:runtime_factory` composes the
configured production context resolver and grounded workflow into a
`WorkflowHostedApplication`. `HOSTED_CONTEXT_RESOLVER_FACTORY=module:callable` and
`HOSTED_GROUNDED_WORKFLOW_FACTORY=module:callable` are **required**, trusted
deployment configuration (never request input). Each provider must be packaged in
the image and return the documented interface. Missing configuration, import
errors and invalid factories fail startup; there is no echo or raw-model fallback.

### Content safety (ADR-0007)

The production composition screens every invocation with Azure AI Content Safety,
like the API. Before any provider loads it builds the Content Safety checker from
`HOSTED_CONTENT_SAFETY_*` settings, which have the same names, defaults and
semantics as the API's `API_CONTENT_SAFETY_*`:

| Setting | Meaning |
| --- | --- |
| `HOSTED_CONTENT_SAFETY_ENDPOINT` | **Required**, HTTPS. Main deployment output `contentSafetyEndpoint`. Startup fails without it. |
| `HOSTED_CONTENT_SAFETY_ENABLED` | Defaults to `true`; `false` is rejected. |
| `HOSTED_CONTENT_SAFETY_TIMEOUT_SECONDS` | Per-call bound, default 5 (also bounded by the context's deadline). |
| `HOSTED_CONTENT_SAFETY_BLOCK_SEVERITY_{HATE,SELF_HARM,SEXUAL,VIOLENCE}` | Block thresholds, 1-6, default 4. |
| `HOSTED_MANAGED_IDENTITY_CLIENT_ID` (or `AZURE_CLIENT_ID`) | Optional user-assigned identity; unset uses the platform identity. |

Authentication is `ManagedIdentityCredential` only (the account disables keys). The
agent's Entra identity needs **Cognitive Services User** on the account: set
`hostedAgentPrincipalId` (`AZURE_HOSTED_AGENT_PRINCIPAL_ID`) to its object ID and
deploy `infrastructure/main.bicep` again. Foundry creates that identity with the
first agent version, so the first deployment refuses every turn
(`content_safety_unavailable`) until the grant exists. That is the fail-closed
behaviour, not a fault.

The workflow factory is called as
`factory(content_safety_checker=..., content_safety_policy=...)` and must pass both
to `GroundedAnswerWorkflow`, which shields the prompt, shields and drops attacked
chunks, and analyses the answer. The application does not trust the factory to do
so: it records every verdict that checker gives during the turn and releases a
result only when the verdicts cover it (a clean Prompt Shields verdict on the
query; for answers, every citation a shielded and unflagged chunk and a
non-blocking analysis of exactly the answer text). Anything else, such as a
factory that ignored the checker, becomes a `content_safety_unavailable` refusal
and logs `hosted_content_safety_unscreened`. Any service error, timeout or
malformed reply also refuses with that code.

Abstentions carry a stable `code`, the same as the API's SSE codes:
`insufficient_evidence`, `content_safety_prompt_attack`,
`content_safety_output_blocked` or `content_safety_unavailable`.

Use `WorkflowHostedApplication(resolver, workflow, screening=...)` from
`accelerator.agent_core.hosting.application`. The resolver verifies the
authorization credential, resolves `ExecutionContext` from server-side
authorization, and raises `InvocationUnauthorized` for missing, invalid, or
unauthorized callers. Its interface is independent of any concrete retrieval
implementation.

The configured workflow provider implements `run(query, ctx)` and can use the
M4 factory contract without adding a second agent loop:
`accelerator.agent_core.agents.factory.AgentFactory(runtime, resolve_tool,
instructions_directory).create(AgentConfig(...))` creates the Microsoft Agent
Framework agent. The provider adapts that agent's generation call to the grounded
workflow's answer-generator port; it must not expose the raw agent endpoint or
bypass evidence sufficiency/citation validation.

The handler forwards only the authorization header to this resolver, never scope,
principal, project or identity fields from JSON or prompts. The only payload is
`{"query":"..."}`. Additional fields are rejected. The application must reject
unverifiable identity if the Foundry gateway does not propagate a usable credential;
do not treat the gateway's own identity as the caller or trust arbitrary forwarded
identity headers. Verify this end-to-end in your deployment's trusted API/gateway
composition before enabling traffic.

Answered outcomes contain nonempty text and retrieved chunk citations. Abstained
outcomes contain only a reason, evidence IDs and a refusal `code`. The hosting wire schema validates this
shape; **same-turn citation membership remains #23's responsibility**.

## Image

From the repository root (Docker Desktop in Linux mode):

```powershell
docker build --platform linux/amd64 -f infrastructure\hosted_agent\Dockerfile -t fde-agent:<git-sha> .
```

The image installs the local agent-core/API packages, #19's pinned Foundry runtime
library, plus the isolated, exact
`infrastructure/hosted_agent/uv.lock`. It runs as UID 10001 without baked-in keys,
credentials, `.env` files or development dependencies. When composition introduces
additional packages, add them to this runtime manifest and regenerate its lock;
don't assume dependencies from another virtual environment exist in the image.

The container is production-shaped but provider-agnostic. Replace both example
factory references with trusted providers packaged in the image before deployment.
The production composition entrypoint is present and fail-closed; it does not
authenticate an identity or retrieve evidence by itself. The offline integration
test exercises the packaged composition and Invocations HTTP contract with one
shared fixture credential. For local runs, supply trusted factories and
model/project values. Never mount developer credentials into the production image.

## Deploy and invoke

External prerequisites: a supported Foundry hosted-agent project/region, a deployed
model, ACR image-pull RBAC on the project's managed identity, and an image pushed to
ACR. #32 outputs `projectEndpoint`, `modelDeploymentName`, `registryLoginServer`
and `registryId` feed the configuration. For ABAC registries grant Container
Registry Repository Reader; otherwise use AcrPull. Pushing needs the corresponding
writer/AcrPush role. The deployer needs Foundry Project Manager on the project.

Foundry creates the hosted agent's dedicated Entra identity. Grant external
Search/Storage/tool permissions separately with least privilege. The runtime
composition must use managed identity/platform workload identity; deployment and
smoke default to `ManagedIdentityCredential`. Explicit `credential_mode=azure-cli`
uses an already authenticated Azure CLI session: local operator `az login`, or an
OIDC-authenticated CI `azure/login` session. It never falls back to keys/secrets.

Copy `deployment.example.json` to your deployment configuration location and
replace every example value. Use an immutable commit tag or image digest (not
`latest`). Keep secrets out of the file. All fields can alternatively be provided
as `HOSTED_` environment variables.

```powershell
uv sync --project infrastructure\hosted_agent --frozen
uv run --project infrastructure\hosted_agent python -m infrastructure.hosted_agent.cli deploy --config <config.json>
uv run --project infrastructure\hosted_agent python -m infrastructure.hosted_agent.cli smoke --config <config.json>
```

Deployment creates a version and polls with a bounded timeout until `active`.
Failed/unknown/terminal status raises an error. It does not change an existing
endpoint pin. Smoke invokes the **agent endpoint**, not a model or health probe,
using `https://ai.azure.com/.default`. HTTP/authentication failures, malformed
results and timeouts are failures; an explicit grounded abstention is a valid
read-only invocation. No model output, credentials or prompts are logged. The
smoke targets the agent endpoint's current routing; verify/promote endpoint routing
separately if production is pinned to an older version.

## Local validation

```powershell
uv run --project infrastructure\hosted_agent --with pytest --with pytest-asyncio pytest infrastructure\hosted_agent\tests
uv run --project infrastructure\hosted_agent --with mypy --with pytest mypy --config-file infrastructure\hosted_agent\pyproject.toml infrastructure packages\agent_core\hosting
make check
make eval-smoke
```

The targeted suite tests the real SDK HTTP adapter with an injected fake
application, fail-closed identity/scope behavior, deployment polling and response
validation without Azure access. A packaged offline-fixture test also composes the
configured resolver/workflow factories and invokes `/invocations` through the
official host protocol in-process. To smoke a built image locally, use the
fixture's exported `OFFLINE_AUTHORIZATION` value as the request Authorization
header; the live Foundry smoke command above separately verifies the deployed
endpoint. Neither offline check claims an Azure deployment.
`make eval-smoke` runs the product's control plane offline (see
`packages/evaluation_core/runners/README.md`); it is not a model evaluation.

## Verified Microsoft Learn references

- [Hosted runtime contract](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agent-contract):
  Linux/amd64, port 8088, `/readiness`, Invocations protocol and injected variables.
- [Deploy a hosted agent](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent):
  `HostedAgentDefinition`, `ContainerConfiguration`, protocol version 2.0.0,
  `create_version`/`get_version`, `active`/`creating` statuses and authenticated
  `/agents/{name}/endpoint/protocols/invocations?api-version=v1`.
- [Framework hosting](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/framework-hosted-agents):
  framework hosts are optional; this transport hosts the explicit workflow
  application instead of exposing an ungrounded Responses agent.

APIs were also checked against the installed, pinned packages. Reserved
`FOUNDRY_*` variables and telemetry connection strings are platform-injected, not
hard-coded in deployment configuration.
