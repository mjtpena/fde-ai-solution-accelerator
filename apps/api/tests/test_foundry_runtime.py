import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from agent_framework import Agent, FunctionTool
from agent_framework.foundry import FoundryChatClient
from azure.core.credentials import TokenCredential
from pydantic import ValidationError

from accelerator.agent_core.agents.factory import AgentConfig, AgentFactory
from accelerator.infrastructure.foundry import AgentFrameworkFoundryRuntime, FoundrySettings


class FoundryRuntimeTests(unittest.TestCase):
    def test_requires_https_project_endpoint(self) -> None:
        for endpoint in ("http://foundry.example", "https://", "https://foundry.example?key=value"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                AgentFrameworkFoundryRuntime(endpoint)

    def test_creates_framework_agent_with_configured_client_and_tools(self) -> None:
        tools = [object()]
        credential = Mock(spec=TokenCredential)
        with (
            patch("accelerator.infrastructure.foundry.agent_runtime.Agent") as agent_constructor,
            patch(
                "accelerator.infrastructure.foundry.agent_runtime.FoundryChatClient"
            ) as client_constructor,
        ):
            runtime = AgentFrameworkFoundryRuntime(
                "https://foundry.example/projects/project",
                credential=credential,
            )

            runtime.create_agent(
                name="assistant",
                model="deployment",
                instructions="Follow the trusted instructions.",
                tools=tools,
            )

        client_constructor.assert_called_once_with(
            project_endpoint="https://foundry.example/projects/project",
            model="deployment",
            credential=credential,
        )
        agent_constructor.assert_called_once_with(
            client=client_constructor.return_value,
            name="assistant",
            instructions="Follow the trusted instructions.",
            tools=tools,
        )

    def test_uses_default_azure_credential_when_not_injected(self) -> None:
        with patch(
            "accelerator.infrastructure.foundry.agent_runtime.DefaultAzureCredential"
        ) as credential:
            runtime = AgentFrameworkFoundryRuntime("https://foundry.example/projects/project")

        credential.assert_called_once_with()
        self.assertIs(runtime._credential, credential.return_value)

    def test_config_builds_real_framework_agent_without_model_or_tool_execution(self) -> None:
        credential = Mock(spec=TokenCredential)
        runtime = AgentFrameworkFoundryRuntime(
            "https://foundry.example/api/projects/project",
            credential=credential,
        )
        declaration = FunctionTool(
            name="lookup",
            description="Declaration of an externally policy-dispatched lookup.",
            func=None,
            input_model={"type": "object", "properties": {}, "additionalProperties": False},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assistant.md").write_text("Trusted file instructions.", encoding="utf-8")
            factory = AgentFactory(runtime, {"lookup": declaration}.__getitem__, root)
            config = AgentConfig.model_validate(
                {
                    "name": "configured-assistant",
                    "model": "configured-deployment",
                    "instructions_file": "assistant.md",
                    "tools": ["lookup"],
                }
            )

            agent = factory.create(config)
            try:
                self.assertIsInstance(agent, Agent)
                self.assertIsInstance(agent.client, FoundryChatClient)
                self.assertEqual(agent.name, config.name)
                self.assertEqual(agent.default_options["model"], config.model)
                self.assertEqual(
                    agent.default_options["instructions"], "Trusted file instructions."
                )
                self.assertEqual(agent.default_options["tools"], [declaration])
                credential.get_token.assert_not_called()
            finally:
                asyncio.run(agent.close())

    def test_config_defaults_to_no_framework_tools(self) -> None:
        runtime = AgentFrameworkFoundryRuntime(
            "https://foundry.example/api/projects/project",
            credential=Mock(spec=TokenCredential),
        )
        resolver = Mock()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assistant.md").write_text("Trusted file instructions.", encoding="utf-8")
            factory = AgentFactory(runtime, resolver, root)
            config = AgentConfig(
                name="assistant", model="deployment", instructions_file="assistant.md"
            )

            agent = factory.create(config)
            try:
                self.assertEqual(agent.default_options["tools"], [])
                resolver.assert_not_called()
            finally:
                asyncio.run(agent.close())


class FoundrySettingsTests(unittest.TestCase):
    def test_reads_https_project_endpoint_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {"FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example/api/projects/project"},
            clear=True,
        ):
            settings = FoundrySettings()

        self.assertEqual(
            str(settings.project_endpoint),
            "https://foundry.example/api/projects/project",
        )

    def test_rejects_missing_or_non_https_project_endpoint(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValidationError):
            FoundrySettings()
        with patch.dict(
            "os.environ",
            {"FOUNDRY_PROJECT_ENDPOINT": "http://foundry.example/api/projects/project"},
            clear=True,
        ), self.assertRaises(ValidationError):
            FoundrySettings()


async def test_runtime_closes_the_clients_it_created() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from accelerator.infrastructure.foundry import agent_runtime

    created = MagicMock()
    created.client.close = AsyncMock()
    created.project_client.close = AsyncMock()
    with patch.object(agent_runtime, "FoundryChatClient", return_value=created), patch.object(
        agent_runtime, "Agent"
    ):
        runtime = AgentFrameworkFoundryRuntime(
            "https://foundry.example.test/api/projects/p", credential=MagicMock()
        )
        runtime.create_agent(name="a", model="m", instructions="i", tools=())
        await runtime.close()
        await runtime.close()

    created.client.close.assert_awaited_once()
    created.project_client.close.assert_awaited_once()
