# Microsoft Foundry runtime setup

Create a Microsoft Foundry project and deploy a chat model. Set
`FOUNDRY_PROJECT_ENDPOINT` to the project's HTTPS endpoint (the
`.../api/projects/<project>` URL) and set each agent configuration's `model`
to the deployed model name. `FoundrySettings.from_environment()` reads and
validates the endpoint; no API key is used.

For local development, authenticate with Azure CLI (`az login`). In a hosted
environment, assign the API's managed identity the required permission to use
the Foundry project and model deployment. The runtime uses
`DefaultAzureCredential` by default and accepts a specific Azure credential
when the host requires one.

`AgentFactory.create()` returns a Microsoft Agent Framework `Agent`; callers
invoke it with `await agent.run(prompt)` and read `response.text`. Instructions
are loaded from a relative Markdown file beneath the configured instructions
directory. Configured tool IDs are resolved by the injected resolver, which
must return Microsoft Agent Framework-compatible tools. Enterprise tools need
a trusted adapter that injects the request's server-resolved execution context
and enforces approval policy; do not pass scope or approval data through the
prompt.

References:

- [Microsoft Foundry model provider](https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/model-providers/microsoft-foundry)
- [First agent and Foundry setup](https://learn.microsoft.com/en-us/agent-framework/get-started/your-first-agent?tabs=python)
- [Agent Framework function tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools?tabs=python)
