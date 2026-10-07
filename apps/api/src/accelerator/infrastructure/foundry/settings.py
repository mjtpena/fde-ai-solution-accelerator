from __future__ import annotations

import os

from pydantic import AnyHttpUrl, field_validator
from pydantic import BaseModel, ConfigDict, TypeAdapter


class FoundrySettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_endpoint: AnyHttpUrl

    @classmethod
    def from_environment(cls) -> FoundrySettings:
        endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
        if endpoint is None:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT must be set")
        validated_endpoint = TypeAdapter(AnyHttpUrl).validate_python(endpoint)
        return cls(project_endpoint=validated_endpoint)

    @field_validator("project_endpoint")
    @classmethod
    def require_https(cls, endpoint: AnyHttpUrl) -> AnyHttpUrl:
        if endpoint.scheme != "https":
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT must use HTTPS")
        return endpoint
