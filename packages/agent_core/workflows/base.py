from abc import ABC, abstractmethod
from typing import Generic, TypeVar


InputT = TypeVar("InputT")
ContextT = TypeVar("ContextT")
OutputT = TypeVar("OutputT")


class Workflow(ABC, Generic[InputT, ContextT, OutputT]):
    @abstractmethod
    async def run(self, workflow_input: InputT, ctx: ContextT) -> OutputT:
        """Run a workflow with trusted execution context."""
