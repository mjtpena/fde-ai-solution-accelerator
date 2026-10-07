# Foundry hosted-agent packaging

Issue #25 packages an **Invocations** agent, not a second agent loop or an Azure
Container App. The official protocol adapter provides port 8088, `/readiness`,
`/invocations` and graceful shutdown. Azure SDK calls are confined to
`azure_adapter.py` behind `HostedAgentGateway`. Resource provisioning belongs to #32.

## Required application composition

The repository does not yet provide a production application composition.
`HOSTED_APPLICATION_FACTORY=module:callable` is **required**, is trusted deployment
configuration (never request input), and returns a `HostedApplication`. Missing
configuration, import errors and invalid factories fail startup; there is no echo
or raw-model fallback.

Use `WorkflowHostedApplication(resolver, workflow)` from
`accelerator.agent_core.hosting.application`. Supply a transport-independent trusted
identity resolver and #23's `GroundedAnswerWorkflow`. The resolver verifies the
authorization credential, resolves `ExecutionContext` from server membership, and
raises `InvocationUnauthorized` for missing/invalid/unauthorized callers. The
workflow's generation port may use #19's `AgentFactory`; never bypass evidence
sufficiency/citation validation by hosting the raw factory agent.

The handler forwards only the authorization header to this resolver, never scope,
principal, project or identity fields from JSON or prompts. The only payload is
`{"query":"..."}`. Additional fields are rejected. The application must reject
unverifiable identity if the Foundry gateway does not propagate a usable credential;
do not treat the gateway's own identity as the caller or trust arbitrary forwarded
identity headers. Verify this end-to-end in your deployment's trusted API/gateway
composition before enabling traffic.

Answered outcomes contain nonempty text and retrieved chunk citations. Abstained
outcomes contain only a reason/evidence IDs. The hosting wire schema validates this
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

The example `accelerator.application.hosted:create_application` is a placeholder,
not an implemented production provider. Replace it with a real provider packaged
in the image before deployment. For a local run supply that factory and model/
project environment values, and arrange explicitly authenticated local identity.
Never mount developer credentials into the production image.

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
validation without Azure access. It is not evidence of a cloud deployment.
On this baseline `make eval-smoke` is still the M5 placeholder, not a model eval.

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
