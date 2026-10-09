from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field, ValidationError

from .. import EnterpriseTool, ExecutionContextProtocol, IdempotentWriteTool, ToolRegistry, ToolRisk


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


class WriteTool(IdempotentWriteTool[Args, Result]):
    name = "write"
    description = "A test fixture demonstrating the approved execution seam."
    risk = ToolRisk.LOW_IMPACT_WRITE
    args_model = Args

    def __init__(self) -> None:
        self.execution_ids: list[UUID] = []

    async def execute_approved(
        self, args: Args, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> Result:
        self.execution_ids.append(execution_id)
        return Result(query=args.query, scope_ids=ctx.scope_ids)


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
    registry = ToolRegistry()
    tool: EnterpriseTool[Args, Result]
    if risk is ToolRisk.READ_ONLY:
        tool = SearchTool()
    else:
        class ClassifiedWriteTool(WriteTool):
            pass

        ClassifiedWriteTool.risk = risk
        tool = ClassifiedWriteTool()
    registry.register(tool)
    assert registry.get(tool.name).risk is risk
    if isinstance(tool, WriteTool):
        assert tool.execution_ids == []
    assert not hasattr(registry, "execute")


@pytest.mark.parametrize(
    "risk", [ToolRisk.LOW_IMPACT_WRITE, ToolRisk.HIGH_IMPACT_WRITE, ToolRisk.PRIVILEGED]
)
def test_writes_without_idempotent_capability_cannot_register(risk: ToolRisk) -> None:
    class UnsafeWriteTool(SearchTool):
        pass

    UnsafeWriteTool.risk = risk
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="IdempotentWriteTool"):
        registry.register(UnsafeWriteTool())
    assert registry.list_tools() == ()


async def test_write_cannot_use_the_unapproved_execution_path() -> None:
    tool = WriteTool()
    with pytest.raises(PermissionError, match="approved execution"):
        await tool.execute(Args(query="change"), ServerContext())
    assert tool.execution_ids == []


async def test_write_receives_the_same_approval_execution_id_on_retry() -> None:
    tool = WriteTool()
    execution_id = uuid4()
    args = Args(query="change")
    ctx = ServerContext()
    await tool.execute_approved(args, ctx, execution_id=execution_id)
    await tool.execute_approved(args, ctx, execution_id=execution_id)
    assert tool.execution_ids == [execution_id, execution_id]


def test_write_capability_requires_an_approved_execution_implementation() -> None:
    class IncompleteWriteTool(IdempotentWriteTool[Args, Result]):
        pass

    with pytest.raises(TypeError, match="abstract"):
        IncompleteWriteTool()  # type: ignore[abstract]


def test_prohibited_idempotent_write_still_cannot_register() -> None:
    class ProhibitedWriteTool(WriteTool):
        risk = ToolRisk.PROHIBITED

    with pytest.raises(ValueError, match="PROHIBITED"):
        ToolRegistry().register(ProhibitedWriteTool())


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
    with pytest.raises(ValueError, match="non-empty|Tool names must match"):
        ToolRegistry().register(InvalidTool())


def test_string_risk_is_not_a_risk_declaration() -> None:
    class InvalidTool(SearchTool):
        pass

    InvalidTool.risk = "read_only"
    with pytest.raises(ValueError, match="ToolRisk"):
        ToolRegistry().register(InvalidTool())


def test_args_model_must_be_a_pydantic_model() -> None:
    class InvalidTool(SearchTool):
        pass

    InvalidTool.args_model = str
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


class AliasedScopeArgs(BaseModel):
    scope: str = Field(alias="scope_id")


class ValidationAliasedProjectArgs(BaseModel):
    project: str = Field(validation_alias=AliasChoices("project", "project_ids"))


class NestedPathScopeArgs(BaseModel):
    scope: str = Field(validation_alias=AliasPath("metadata", "scope_ids"))


@pytest.mark.parametrize("model", [ScopeIdArgs, ScopeIdsArgs, ProjectIdArgs, ProjectIdsArgs])
def test_scope_arguments_cannot_register(model: type[BaseModel]) -> None:
    class ScopedTool(SearchTool):
        args_model = model

    with pytest.raises(ValueError, match="ExecutionContext"):
        ToolRegistry().register(ScopedTool())


@pytest.mark.parametrize(
    "model",
    [AliasedScopeArgs, ValidationAliasedProjectArgs, NestedPathScopeArgs],
)
def test_scope_arguments_cannot_register_through_validation_aliases(
    model: type[BaseModel],
) -> None:
    class AliasedScopedTool(SearchTool):
        args_model = model

    with pytest.raises(ValueError, match="ExecutionContext"):
        ToolRegistry().register(AliasedScopedTool())


class ExtraAllowedArgs(BaseModel):
    model_config = ConfigDict(extra="allow")
    query: str


def test_args_models_cannot_allow_undeclared_scope_fields() -> None:
    class ExtraAllowedTool(SearchTool):
        args_model = ExtraAllowedArgs

    with pytest.raises(ValueError, match="cannot allow undeclared extra"):
        ToolRegistry().register(ExtraAllowedTool())


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

    InvalidTimeout.timeout_seconds = timeout
    with pytest.raises(ValueError, match="finite and positive"):
        ToolRegistry().register(InvalidTimeout())


@pytest.mark.parametrize("name", ["has space", "x" * 129, "semi;colon", ""])
def test_tool_names_outside_the_audit_contract_are_rejected(name: str) -> None:
    class Named(SearchTool):
        pass

    Named.name = name
    with pytest.raises(ValueError, match="Tool names must match"):
        ToolRegistry().register(Named())
