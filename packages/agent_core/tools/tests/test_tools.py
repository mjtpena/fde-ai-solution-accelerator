from datetime import UTC, datetime
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from .. import EnterpriseTool, ExecutionContextProtocol, ToolRegistry, ToolRisk


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


class Result(BaseModel):
    query: str
    scope_ids: frozenset[str]


class ServerContext(BaseModel):
    correlation_id: str = "correlation"
    user_id: str = "requester"
    roles: frozenset[str] = frozenset({"reader"})
    scope_ids: frozenset[str] = frozenset({"server-scope"})
    session_id: str | None = None
    deadline_utc: datetime = datetime(2099, 1, 1, tzinfo=UTC)


class SearchTool(EnterpriseTool[Args, Result]):
    name = "search"
    description = "Search accessible content."
    risk = ToolRisk.READ_ONLY
    args_model: ClassVar[type[BaseModel]] = Args

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, args: Args, ctx: ExecutionContextProtocol) -> Result:
        self.calls += 1
        return Result(query=args.query, scope_ids=ctx.scope_ids)


class OtherArgs(BaseModel):
    count: int


class OtherResult(BaseModel):
    count: int


class OtherTool(EnterpriseTool[OtherArgs, OtherResult]):
    name = "other"
    description = "A tool with different argument and result types."
    risk = ToolRisk.READ_ONLY
    args_model: ClassVar[type[BaseModel]] = OtherArgs

    async def execute(self, args: OtherArgs, ctx: ExecutionContextProtocol) -> OtherResult:
        return OtherResult(count=args.count)


def test_enterprise_tool_requires_an_execute_implementation() -> None:
    class AbstractTool(EnterpriseTool[Args, Result]):
        pass

    with pytest.raises(TypeError, match="abstract"):
        AbstractTool()  # type: ignore[abstract]


def test_risk_values_match_the_spec() -> None:
    assert {risk.value for risk in ToolRisk} == {
        "read_only", "low_impact_write", "high_impact_write", "privileged", "prohibited"
    }


async def test_typed_tool_reads_scope_from_server_context() -> None:
    tool = SearchTool()
    ctx: ExecutionContextProtocol = ServerContext()
    result = await tool.execute(Args(query="content"), ctx)
    assert isinstance(result, Result)
    assert result.query == "content"
    assert result.scope_ids == frozenset({"server-scope"})
    assert tool.timeout_seconds == 20.0
    with pytest.raises(ValidationError):
        Args.model_validate({"query": "content", "scope_ids": ["untrusted"]})


def test_registry_discovers_heterogeneous_tools_without_executing_them() -> None:
    registry = ToolRegistry()
    first = SearchTool()
    second = OtherTool()
    registry.register(first)
    snapshot = registry.list_tools()
    registry.register(second)
    assert registry.get("search") is first
    assert registry.get("other") is second
    assert registry.list_tools() == (first, second)
    assert snapshot == (first,)
    assert first.calls == 0


@pytest.mark.parametrize("risk", [risk for risk in ToolRisk if risk is not ToolRisk.PROHIBITED])
def test_registration_preserves_risk_without_granting_execution(risk: ToolRisk) -> None:
    class ClassifiedTool(SearchTool):
        pass

    ClassifiedTool.risk = risk
    registry = ToolRegistry()
    tool = ClassifiedTool()
    registry.register(tool)
    assert registry.get(tool.name).risk is risk
    assert tool.calls == 0
    assert not hasattr(registry, "execute")


def test_policy_rejects_prohibited_tool_without_changing_registry() -> None:
    class ProhibitedTool(SearchTool):
        name = "prohibited"
        risk = ToolRisk.PROHIBITED

    registry = ToolRegistry()
    allowed = SearchTool()
    registry.register(allowed)
    prohibited = ProhibitedTool()
    with pytest.raises(ValueError, match="PROHIBITED"):
        registry.register(prohibited)
    assert registry.list_tools() == (allowed,)
    with pytest.raises(KeyError):
        registry.get(prohibited.name)
    assert prohibited.calls == 0


def test_duplicate_registration_does_not_replace_the_original_tool() -> None:
    registry = ToolRegistry()
    original = SearchTool()
    registry.register(original)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(SearchTool())
    assert registry.get(original.name) is original


def test_unknown_tool_name_fails_explicitly() -> None:
    with pytest.raises(KeyError, match="missing"):
        ToolRegistry().get("missing")


def test_non_enterprise_tool_is_rejected() -> None:
    with pytest.raises(TypeError, match="EnterpriseTool"):
        ToolRegistry().register(object())  # type: ignore[arg-type]


def test_missing_tool_metadata_is_rejected() -> None:
    class MissingMetadata(EnterpriseTool[Args, Result]):
        async def execute(self, args: Args, ctx: ExecutionContextProtocol) -> Result:
            return Result(query=args.query, scope_ids=ctx.scope_ids)

    with pytest.raises(ValueError, match="ToolRisk"):
        ToolRegistry().register(MissingMetadata())


@pytest.mark.parametrize("field", ["name", "description"])
def test_blank_metadata_is_rejected(field: str) -> None:
    class InvalidTool(SearchTool):
        pass

    setattr(InvalidTool, field, " ")
    with pytest.raises(ValueError, match="non-empty"):
        ToolRegistry().register(InvalidTool())


def test_string_risk_is_not_a_risk_declaration() -> None:
    class InvalidTool(SearchTool):
        pass

    setattr(InvalidTool, "risk", "read_only")
    with pytest.raises(ValueError, match="ToolRisk"):
        ToolRegistry().register(InvalidTool())


def test_args_model_must_be_a_pydantic_model() -> None:
    class InvalidTool(SearchTool):
        pass

    setattr(InvalidTool, "args_model", str)
    with pytest.raises(ValueError, match="Pydantic"):
        ToolRegistry().register(InvalidTool())


class ScopeIdArgs(BaseModel):
    scope_id: str


class ScopeIdsArgs(BaseModel):
    scope_ids: frozenset[str]


class ProjectIdArgs(BaseModel):
    project_id: str


class ProjectIdsArgs(BaseModel):
    project_ids: frozenset[str]


@pytest.mark.parametrize("model", [ScopeIdArgs, ScopeIdsArgs, ProjectIdArgs, ProjectIdsArgs])
def test_scope_arguments_cannot_register(model: type[BaseModel]) -> None:
    class ScopedTool(SearchTool):
        args_model = model

    with pytest.raises(ValueError, match="ExecutionContext"):
        ToolRegistry().register(ScopedTool())


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_timeouts_cannot_register(timeout: float) -> None:
    class InvalidTimeout(SearchTool):
        timeout_seconds: ClassVar[float] = timeout

    with pytest.raises(ValueError, match="finite and positive"):
        ToolRegistry().register(InvalidTimeout())


@pytest.mark.parametrize("timeout", [True, "20"])
def test_non_numeric_timeouts_cannot_register(timeout: object) -> None:
    class InvalidTimeout(SearchTool):
        pass

    setattr(InvalidTimeout, "timeout_seconds", timeout)
    with pytest.raises(ValueError, match="finite and positive"):
        ToolRegistry().register(InvalidTimeout())
