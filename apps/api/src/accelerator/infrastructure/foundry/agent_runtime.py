from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.core.credentials import TokenCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import DefaultAzureCredential


class AgentFrameworkFoundryRuntime:
    def __init__(
        self,
        project_endpoint: str,
        *,
        credential: TokenCredential | AsyncTokenCredential | None = None,
    ) -> None:
        endpoint = urlsplit(project_endpoint)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("project_endpoint must be an HTTPS URL without credentials or query data")
        self._project_endpoint = project_endpoint
        self._credential: TokenCredential | AsyncTokenCredential = (
            credential if credential is not None else DefaultAzureCredential()
        )

    def create_agent(
        self,
        *,
        name: str,
        model: str,
        instructions: str,
        tools: Sequence[Any],
    ) -> Agent:
        client = FoundryChatClient(
            project_endpoint=self._project_endpoint,
            model=model,
            credential=self._credential,
        )
        return Agent(
            client=client,
            name=name,
            instructions=instructions,
            tools=list(tools),
        )
