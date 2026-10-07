from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_")

    environment: str = Field(min_length=1)
    diagnostics_include_content: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
