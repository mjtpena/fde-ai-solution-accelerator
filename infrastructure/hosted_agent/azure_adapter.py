"""Azure SDK and authenticated HTTP calls behind the hosted-agent gateway."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointProtocol,
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

from accelerator.agent_core.hosting.contracts import InvocationResult
from infrastructure.hosted_agent.configuration import DeploymentSettings
from infrastructure.hosted_agent.service import HostedVersion


class AzureHostedAgentGateway:
    def __init__(
        self,
        project: AIProjectClient,
        credential: AsyncTokenCredential,
        http: httpx.AsyncClient,
        endpoint: str,
    ) -> None:
        self._project = project
        self._credential = credential
        self._http = http
        self._endpoint = endpoint

    async def create_version(self, settings: DeploymentSettings) -> HostedVersion:
        created = await self._project.agents.create_version(
            agent_name=settings.agent_name,
            definition=HostedAgentDefinition(
                cpu=settings.cpu,
                memory=settings.memory,
                protocol_versions=[
                    ProtocolVersionRecord(
                        protocol=AgentEndpointProtocol.INVOCATIONS, version="2.0.0"
                    )
                ],
                container_configuration=ContainerConfiguration(image=settings.image),
                environment_variables=settings.runtime_environment(),
            ),
        )
        return HostedVersion(name=created.name, version=created.version, status=str(created.status))

    async def get_version(self, name: str, version: str) -> HostedVersion:
        current = await self._project.agents.get_version(agent_name=name, agent_version=version)
        return HostedVersion(name=current.name, version=current.version, status=str(current.status))

    async def invoke(self, name: str, query: str) -> InvocationResult:
        # Agent names come from validated settings, never request payloads.
        token = await self._credential.get_token("https://ai.azure.com/.default")
        response = await self._http.post(
            f"{self._endpoint}/agents/{name}/endpoint/protocols/invocations",
            params={"api-version": "v1"},
            headers={"Authorization": f"Bearer {token.token}"},
            json={"query": query},
        )
        response.raise_for_status()
        return InvocationResult.model_validate_json(response.content)


@asynccontextmanager
async def open_gateway(settings: DeploymentSettings) -> AsyncIterator[AzureHostedAgentGateway]:
    credential: ManagedIdentityCredential | AzureCliCredential
    if settings.credential_mode == "azure-cli":
        credential = AzureCliCredential()
    else:
        credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    async with credential:
        async with AIProjectClient(
            endpoint=settings.project_endpoint, credential=credential
        ) as project:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds) as http:
                yield AzureHostedAgentGateway(project, credential, http, settings.project_endpoint)
