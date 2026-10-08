from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

AgentT_co = TypeVar("AgentT_co", covariant=True)
AgentT = TypeVar("AgentT")


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    instructions_file: str = Field(min_length=1)
    tools: tuple[Annotated[str, Field(min_length=1)], ...] = ()


class AgentRuntime(Protocol[AgentT_co]):
    def create_agent(
        self,
        *,
        name: str,
        model: str,
        instructions: str,
        tools: Sequence[Any],
    ) -> AgentT_co: ...


class AgentFactory(Generic[AgentT]):
    """Build agents from trusted config and an adapter-backed runtime."""

    def __init__(
        self,
        runtime: AgentRuntime[AgentT],
        resolve_tool: Callable[[str], Any],
        instructions_directory: Path,
    ) -> None:
        """Resolve tool IDs to Microsoft Agent Framework-compatible tools."""
        self._runtime = runtime
        self._resolve_tool = resolve_tool
        self._instructions_directory = instructions_directory.resolve()

    def create(self, config: AgentConfig) -> AgentT:
        instructions_path = self._resolve_instructions_path(config.instructions_file)
        instructions = instructions_path.read_text(encoding="utf-8")
        if not instructions.strip():
            raise ValueError(f"Instructions file is empty: {config.instructions_file}")

        tools = tuple(self._resolve_tool(name) for name in config.tools)
        return self._runtime.create_agent(
            name=config.name,
            model=config.model,
            instructions=instructions,
            tools=tools,
        )

    def _resolve_instructions_path(self, filename: str) -> Path:
        relative_path = Path(filename)
        if relative_path.is_absolute() or relative_path.suffix.lower() != ".md":
            raise ValueError("instructions_file must be a relative Markdown file path")

        path = (self._instructions_directory / relative_path).resolve()
        try:
            path.relative_to(self._instructions_directory)
        except ValueError as exc:
            raise ValueError("instructions_file must stay inside the instructions directory") from exc
        if not path.is_file():
            raise FileNotFoundError(f"Instructions file not found: {filename}")
        return path
