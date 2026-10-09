from typing import Annotated, Literal

from opentelemetry.util.types import AttributeValue
from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
Count = Annotated[int, Field(ge=0)]


class SpanAttributes(BaseModel):
    """Only metadata is accepted; content and raw filter values have no fields."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    filter_fields: tuple[Identifier, ...] | None = None
    top_k: Annotated[int, Field(gt=0)] | None = None
    result_count: Count | None = None
    decision: Literal["sufficient", "insufficient", "abstain"] | None = None
    reason_code: Identifier | None = None
    model: Identifier | None = None
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    risk: Literal["read", "write", "destructive"] | None = None
    outcome: Literal["success", "failure", "denied", "abstained"] | None = None
    citation_count: Count | None = None
    valid: bool | None = None

    def to_otel(self) -> dict[str, AttributeValue]:
        names = {
            "filter_fields": "fde.retrieval.filter_fields",
            "top_k": "fde.retrieval.top_k",
            "result_count": "fde.retrieval.result_count",
            "decision": "fde.retrieval.decision",
            "reason_code": "fde.retrieval.reason_code",
            "model": "gen_ai.request.model",
            "input_tokens": "gen_ai.usage.input_tokens",
            "output_tokens": "gen_ai.usage.output_tokens",
            "risk": "fde.tool.risk",
            "outcome": "fde.outcome",
            "citation_count": "fde.citations.count",
            "valid": "fde.citations.valid",
        }
        return {
            names[name]: value
            for name, value in self.model_dump(exclude_none=True).items()
        }
