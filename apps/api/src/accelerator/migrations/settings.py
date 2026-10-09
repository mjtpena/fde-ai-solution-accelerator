from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """Only what a migration run needs; never the API's identity-provider settings."""

    model_config = SettingsConfigDict(env_prefix="API_")

    database_url: PostgresDsn
    database_auth_mode: Literal["password", "managed_identity"] = "password"
    database_connect_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
