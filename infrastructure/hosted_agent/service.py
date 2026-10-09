"""Deployment lifecycle and smoke validation, independent of Azure SDK classes."""

import asyncio
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from accelerator.agent_core.hosting.contracts import InvocationResult
from infrastructure.hosted_agent.configuration import DeploymentSettings


class HostedVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: str


class HostedAgentGateway(Protocol):
    async def create_version(self, settings: DeploymentSettings) -> HostedVersion: ...

    async def get_version(self, name: str, version: str) -> HostedVersion: ...

    async def invoke(self, name: str, query: str) -> InvocationResult: ...


async def deploy(gateway: HostedAgentGateway, settings: DeploymentSettings) -> HostedVersion:
    version = await gateway.create_version(settings)
    async with asyncio.timeout(settings.timeout_seconds):
        while True:
            current = await gateway.get_version(version.name, version.version)
            if current.status == "active":
                return current
            if current.status != "creating":
                raise RuntimeError(f"Hosted agent provisioning ended with status {current.status}")
            await asyncio.sleep(settings.poll_seconds)


async def smoke(gateway: HostedAgentGateway, name: str, timeout_seconds: float) -> InvocationResult:
    async with asyncio.timeout(timeout_seconds):
        return await gateway.invoke(
            name, "Return a grounded answer if evidence is available; otherwise abstain."
        )
