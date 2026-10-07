import unittest
from unittest.mock import Mock, patch

from azure.core.credentials import TokenCredential
from pydantic import ValidationError

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
            patch("accelerator.infrastructure.foundry.agent_runtime.FoundryChatClient") as client_constructor,
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
        with patch("accelerator.infrastructure.foundry.agent_runtime.DefaultAzureCredential") as credential:
            runtime = AgentFrameworkFoundryRuntime("https://foundry.example/projects/project")

        credential.assert_called_once_with()
        self.assertIs(runtime._credential, credential.return_value)


class FoundrySettingsTests(unittest.TestCase):
    def test_reads_https_project_endpoint_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {"FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example/api/projects/project"},
            clear=True,
        ):
            settings = FoundrySettings.from_environment()

        self.assertEqual(
            str(settings.project_endpoint),
            "https://foundry.example/api/projects/project",
        )

    def test_rejects_missing_or_non_https_project_endpoint(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            FoundrySettings.from_environment()
        with patch.dict(
            "os.environ",
            {"FOUNDRY_PROJECT_ENDPOINT": "http://foundry.example/api/projects/project"},
            clear=True,
        ), self.assertRaises(ValidationError):
            FoundrySettings.from_environment()
