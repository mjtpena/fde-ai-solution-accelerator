from __future__ import annotations

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FoundrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FOUNDRY_", frozen=True)

    project_endpoint: AnyHttpUrl

    @field_validator("project_endpoint")
    @classmethod
    def require_https(cls, endpoint: AnyHttpUrl) -> AnyHttpUrl:
        if endpoint.scheme != "https":
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT must use HTTPS")
        return endpoint
