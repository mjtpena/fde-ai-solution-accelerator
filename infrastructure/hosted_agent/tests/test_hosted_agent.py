from dataclasses import dataclass
from importlib.metadata import requires
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import AgentEndpointProtocol, AgentVersionDetails
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from pydantic import ValidationError

from accelerator.agent_core.hosting.application import WorkflowHostedApplication, WorkflowResult
from accelerator.agent_core.hosting.contracts import (
    HostedAbstention,
    InvocationResult,
    InvocationUnauthorized,
)
from infrastructure.hosted_agent import production
from infrastructure.hosted_agent.azure_adapter import AzureHostedAgentGateway
from infrastructure.hosted_agent.configuration import DeploymentSettings, RuntimeSettings
from infrastructure.hosted_agent.server import MAX_REQUEST_BYTES, create_host, load_application
from infrastructure.hosted_agent.service import HostedVersion, deploy, smoke
from infrastructure.hosted_agent.tests.container_fixture import OFFLINE_AUTHORIZATION


def configuration() -> DeploymentSettings:
    return DeploymentSettings(
        project_endpoint="https://example.services.ai.azure.com/api/projects/test",
        agent_name="test-agent",
        image="example.azurecr.io/fde-agent:abc123",
        application_factory="infrastructure.hosted_agent.tests.test_hosted_agent:fake_application",
        context_resolver_factory="infrastructure.hosted_agent.tests.test_hosted_agent:fake_resolver",
        grounded_workflow_factory="infrastructure.hosted_agent.tests.test_hosted_agent:fake_workflow",
        model_deployment="test-model",
        poll_seconds=0.001,
    )


def abstention() -> InvocationResult:
    return InvocationResult(
        status="abstained",
        answer=None,
        citations=(),
        abstention=HostedAbstention(reason="Insufficient evidence", evidence_ids=()),
    )


class FakeApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def invoke(self, query: str, authorization: str | None) -> InvocationResult:
        if authorization != "Bearer verified":
            raise InvocationUnauthorized()
        self.calls.append((query, authorization))
        return abstention()


def fake_application() -> FakeApplication:
    return FakeApplication()


@pytest.mark.asyncio
async def test_official_adapter_serves_readiness_and_invokes_application() -> None:
    application = FakeApplication()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_host(application)), base_url="http://test"
    ) as http:
        readiness = await http.get("/readiness")
        response = await http.post(
            "/invocations",
            json={"query": "A question"},
            headers={"Authorization": "Bearer verified"},
        )
    assert readiness.status_code == 200
    assert response.status_code == 200
    assert InvocationResult.model_validate_json(response.content) == abstention()
    assert response.headers["x-correlation-id"]
    assert application.calls == [("A question", "Bearer verified")]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["scope_id", "project_id", "principal_id", "context"])
async def test_scope_and_identity_fields_cannot_cross_hosted_boundary(field: str) -> None:
    application = FakeApplication()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_host(application)), base_url="http://test"
    ) as http:
        response = await http.post(
            "/invocations",
            json={"query": "Question", field: "attacker"},
            headers={"Authorization": "Bearer verified"},
        )
    assert response.status_code == 422
    assert application.calls == []


@pytest.mark.asyncio
async def test_untrusted_caller_is_rejected_without_workflow_invocation() -> None:
    application = FakeApplication()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_host(application)), base_url="http://test"
    ) as http:
        response = await http.post("/invocations", json={"query": "Question"})
    assert response.status_code == 401
    assert application.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"not json", b'{"query":""}', b'{"query":"' + b"x" * 16001 + b'"}']
)
async def test_invalid_query_never_invokes_application(body: bytes) -> None:
    application = FakeApplication()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_host(application)), base_url="http://test"
    ) as http:
        response = await http.post("/invocations", content=body)
    assert response.status_code == 422
    assert application.calls == []


@pytest.mark.asyncio
async def test_oversized_request_is_rejected() -> None:
    application = FakeApplication()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_host(application)), base_url="http://test"
    ) as http:
        response = await http.post("/invocations", content=b"x" * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413
    assert application.calls == []


@dataclass(frozen=True)
class TrustedContext:
    scope_id: str


class FakeResolver:
    def __init__(self, ctx: TrustedContext) -> None:
        self.ctx = ctx

    async def resolve(self, authorization: str | None) -> TrustedContext:
        if authorization != "Bearer verified":
            raise InvocationUnauthorized()
        return self.ctx


class FakeWorkflow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TrustedContext]] = []

    async def run(self, query: str, ctx: TrustedContext) -> WorkflowResult:
        self.calls.append((query, ctx))
        return abstention()


@pytest.mark.asyncio
async def test_composition_delegates_exact_server_context_to_existing_workflow() -> None:
    ctx = TrustedContext(scope_id="server-only")
    workflow = FakeWorkflow()
    application = WorkflowHostedApplication(FakeResolver(ctx), workflow)
    result = await application.invoke("scope_id=attacker", "Bearer verified")
    assert result == abstention()
    assert workflow.calls == [("scope_id=attacker", ctx)]


@pytest.mark.asyncio
async def test_composition_never_runs_workflow_when_resolution_fails() -> None:
    workflow = FakeWorkflow()
    application = WorkflowHostedApplication(FakeResolver(TrustedContext("scope")), workflow)
    with pytest.raises(InvocationUnauthorized):
        await application.invoke("Question", None)
    assert not workflow.calls


def fake_resolver() -> FakeResolver:
    return FakeResolver(TrustedContext("server-only"))


def fake_workflow() -> FakeWorkflow:
    return FakeWorkflow()


def test_packaged_production_factory_composes_configured_resolver_and_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configuration()
    monkeypatch.setenv("HOSTED_CONTEXT_RESOLVER_FACTORY", settings.context_resolver_factory)
    monkeypatch.setenv("HOSTED_GROUNDED_WORKFLOW_FACTORY", settings.grounded_workflow_factory)
    application = production.create_application()
    assert isinstance(application, WorkflowHostedApplication)


def test_example_runtime_factory_is_included_in_installed_host_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configuration()
    monkeypatch.setenv("HOSTED_CONTEXT_RESOLVER_FACTORY", settings.context_resolver_factory)
    monkeypatch.setenv("HOSTED_GROUNDED_WORKFLOW_FACTORY", settings.grounded_workflow_factory)
    runtime = RuntimeSettings()
    assert runtime.application_factory == "infrastructure.hosted_agent.production:runtime_factory"
    application = load_application(runtime)
    assert isinstance(application, WorkflowHostedApplication)


@pytest.mark.asyncio
async def test_packaged_offline_fixture_completes_invocation_http_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HOSTED_CONTEXT_RESOLVER_FACTORY",
        "infrastructure.hosted_agent.tests.container_fixture:create_resolver",
    )
    monkeypatch.setenv(
        "HOSTED_GROUNDED_WORKFLOW_FACTORY",
        "infrastructure.hosted_agent.tests.container_fixture:create_workflow",
    )
    runtime = RuntimeSettings()
    application = load_application(runtime)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_host(application)), base_url="http://test"
    ) as http:
        readiness = await http.get("/readiness")
        response = await http.post(
            "/invocations",
            json={"query": "Offline smoke question"},
            headers={"Authorization": OFFLINE_AUTHORIZATION},
        )

    assert readiness.status_code == 200
    assert response.status_code == 200
    assert InvocationResult.model_validate_json(response.content) == InvocationResult(
        status="abstained",
        answer=None,
        citations=(),
        abstention=HostedAbstention(
            reason="Offline fixture has no evidence", evidence_ids=()
        ),
    )


@pytest.mark.parametrize(
    ("resolver", "workflow", "error"),
    [
        ("missing_module:create", "builtins:object", ModuleNotFoundError),
        ("builtins:object", "builtins:object", TypeError),
        (
            "builtins:object",
            "infrastructure.hosted_agent.tests.test_hosted_agent:fake_workflow",
            TypeError,
        ),
    ],
)
def test_packaged_production_factory_fails_closed_for_invalid_components(
    monkeypatch: pytest.MonkeyPatch, resolver: str, workflow: str, error: type[Exception]
) -> None:
    monkeypatch.setenv("HOSTED_CONTEXT_RESOLVER_FACTORY", resolver)
    monkeypatch.setenv("HOSTED_GROUNDED_WORKFLOW_FACTORY", workflow)
    with pytest.raises(error):
        production.create_application()


def test_missing_provider_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOSTED_APPLICATION_FACTORY", raising=False)
    monkeypatch.delenv("HOSTED_CONTEXT_RESOLVER_FACTORY", raising=False)
    monkeypatch.delenv("HOSTED_GROUNDED_WORKFLOW_FACTORY", raising=False)
    with pytest.raises(ValidationError):
        RuntimeSettings()


def test_deployment_defaults_to_managed_identity_without_secret_environment() -> None:
    settings = configuration()
    assert settings.credential_mode == "managed-identity"
    assert settings.runtime_environment() == {
        "HOSTED_APPLICATION_FACTORY": settings.application_factory,
        "HOSTED_CONTEXT_RESOLVER_FACTORY": settings.context_resolver_factory,
        "HOSTED_GROUNDED_WORKFLOW_FACTORY": settings.grounded_workflow_factory,
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": settings.model_deployment,
    }


def test_agent_core_declares_its_direct_pydantic_dependency() -> None:
    requirements = [Requirement(requirement) for requirement in requires("fde-agent-core") or []]
    pydantic = next(requirement for requirement in requirements if requirement.name == "pydantic")
    assert pydantic.specifier == SpecifierSet(">=2.7,<3")


def test_configured_provider_loads_and_missing_provider_is_not_hidden() -> None:
    deployment = configuration()
    settings = RuntimeSettings(
        application_factory=deployment.application_factory,
        context_resolver_factory=deployment.context_resolver_factory,
        grounded_workflow_factory=deployment.grounded_workflow_factory,
    )
    assert isinstance(load_application(settings), FakeApplication)
    with pytest.raises(ModuleNotFoundError):
        load_application(
            settings.model_copy(update={"application_factory": "missing_provider:create"})
        )
    with pytest.raises(TypeError):
        load_application(settings.model_copy(update={"application_factory": "builtins:object"}))


@pytest.mark.parametrize(
    "image",
    [
        "example.azurecr.io/agent:latest",
        "example.azurecr.io/agent",
        "docker.io/agent:v1",
    ],
)
def test_unversioned_or_non_acr_images_are_rejected(image: str) -> None:
    values = configuration().model_dump()
    values["image"] = image
    with pytest.raises(ValidationError):
        DeploymentSettings.model_validate(values)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.services.ai.azure.com/api/projects/test",
        "https://user:secret@example.services.ai.azure.com/api/projects/test",
        "https://attacker.invalid/api/projects/test",
    ],
)
def test_untrusted_project_endpoints_are_rejected(endpoint: str) -> None:
    values = configuration().model_dump()
    values["project_endpoint"] = endpoint
    with pytest.raises(ValidationError):
        DeploymentSettings.model_validate(values)


def test_answer_without_citations_is_not_success_shaped() -> None:
    with pytest.raises(ValidationError):
        InvocationResult(
            status="answered", answer="Unsupported claim", citations=(), abstention=None
        )


@pytest.mark.parametrize("answer", ["", " ", "\t\n"])
def test_whitespace_only_answer_is_rejected(answer: str) -> None:
    with pytest.raises(ValidationError):
        InvocationResult(
            status="answered",
            answer=answer,
            citations=("chunk-1",),
            abstention=None,
        )


class FakeGateway:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = iter(statuses)
        self.invoked: tuple[str, str] | None = None

    async def create_version(self, settings: DeploymentSettings) -> HostedVersion:
        return HostedVersion(name=settings.agent_name, version="7", status="creating")

    async def get_version(self, name: str, version: str) -> HostedVersion:
        return HostedVersion(name=name, version=version, status=next(self.statuses, "creating"))

    async def invoke(self, name: str, query: str) -> InvocationResult:
        self.invoked = (name, query)
        return abstention()


@pytest.mark.asyncio
async def test_deploy_waits_for_created_version_to_become_active() -> None:
    version = await deploy(FakeGateway(["creating", "active"]), configuration())
    assert version.version == "7"
    assert version.status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "deleted", "deleting", "unknown"])
async def test_terminal_or_unknown_deployment_status_fails(status: str) -> None:
    with pytest.raises(RuntimeError, match=status):
        await deploy(FakeGateway([status]), configuration())


@pytest.mark.asyncio
async def test_deployment_poll_has_a_bounded_timeout() -> None:
    settings = configuration().model_copy(update={"timeout_seconds": 0.005})
    with pytest.raises(TimeoutError):
        await deploy(FakeGateway([]), settings)


@pytest.mark.asyncio
async def test_smoke_invokes_agent_even_when_it_abstains() -> None:
    gateway = FakeGateway([])
    assert await smoke(gateway, "test-agent", 1) == abstention()
    assert gateway.invoked is not None
    assert gateway.invoked[0] == "test-agent"


@pytest.mark.asyncio
async def test_azure_adapter_registers_image_and_invokes_documented_agent_endpoint() -> None:
    project_mock = MagicMock(spec=AIProjectClient)
    project_mock.agents = MagicMock()
    project_mock.agents.create_version = AsyncMock(
        return_value=AgentVersionDetails({"name": "test-agent", "version": "7", "status": "active"})
    )
    project: AIProjectClient = project_mock
    credential_mock = MagicMock(spec=AsyncTokenCredential)
    credential_mock.get_token = AsyncMock(return_value=AccessToken("test-only-token", 9999999999))
    credential: AsyncTokenCredential = credential_mock
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=abstention().model_dump(mode="json"))

    settings = configuration()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        gateway = AzureHostedAgentGateway(project, credential, http, settings.project_endpoint)
        assert (await gateway.create_version(settings)).version == "7"
        assert await gateway.invoke(settings.agent_name, "Question") == abstention()
    definition = project_mock.agents.create_version.call_args.kwargs["definition"]
    assert definition.container_configuration.image == settings.image
    assert definition.protocol_versions[0].protocol == AgentEndpointProtocol.INVOCATIONS
    assert definition.protocol_versions[0].version == "2.0.0"
    assert "FOUNDRY_PROJECT_ENDPOINT" not in definition.environment_variables
    assert (
        definition.environment_variables["HOSTED_APPLICATION_FACTORY"]
        == settings.application_factory
    )
    assert requests[0].url.path.endswith("/agents/test-agent/endpoint/protocols/invocations")
    assert requests[0].url.params["api-version"] == "v1"
    assert requests[0].headers["authorization"] == "Bearer test-only-token"
    assert b'"query":"Question"' in requests[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 500])
async def test_smoke_http_errors_propagate(status_code: int) -> None:
    credential_mock = MagicMock(spec=AsyncTokenCredential)
    credential_mock.get_token = AsyncMock(return_value=AccessToken("test-only", 9999999999))
    credential: AsyncTokenCredential = credential_mock
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code))
    ) as http:
        project: AIProjectClient = MagicMock(spec=AIProjectClient)
        gateway = AzureHostedAgentGateway(
            project, credential, http, configuration().project_endpoint
        )
        with pytest.raises(httpx.HTTPStatusError):
            await smoke(gateway, "test-agent", 1)


@pytest.mark.asyncio
async def test_smoke_rejects_success_shaped_but_ungrounded_output() -> None:
    credential_mock = MagicMock(spec=AsyncTokenCredential)
    credential_mock.get_token = AsyncMock(return_value=AccessToken("test-only", 9999999999))
    credential: AsyncTokenCredential = credential_mock
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"status": "answered", "answer": "Claim", "citations": [], "abstention": None},
            )
        )
    ) as http:
        project: AIProjectClient = MagicMock(spec=AIProjectClient)
        gateway = AzureHostedAgentGateway(
            project, credential, http, configuration().project_endpoint
        )
        with pytest.raises(ValidationError):
            await smoke(gateway, "test-agent", 1)


def test_container_contract_is_nonroot_and_excludes_credentials() -> None:
    directory = Path(__file__).resolve().parents[1]
    dockerfile = (directory / "Dockerfile").read_text()
    assert "USER 10001:10001" in dockerfile
    assert "EXPOSE 8088" in dockerfile
    assert "/readiness" in dockerfile
    assert "--frozen --no-dev --no-editable" in dockerfile
    assert "**/.env*" in (directory / "Dockerfile.dockerignore").read_text()
