from functools import lru_cache
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_")

    environment: str = Field(min_length=1)
    entra_tenant_id: UUID
    entra_audience: str = Field(min_length=1)
    web_origin: str = Field(default="http://localhost:3000", min_length=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
