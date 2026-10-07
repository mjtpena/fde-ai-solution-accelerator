from typing import Literal

from pydantic import BaseModel, ConfigDict


class DatasetRow(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    category: Literal[
        "factual", "synthesis", "conflict", "unsupported", "tool_selection", "injection"
    ]
    query: str
    scope_id: str
    expected_answer: str | None
    expected_evidence_ids: list[str]
    expected_tool: str | None
    expected_abstain: bool
    tags: list[str]
