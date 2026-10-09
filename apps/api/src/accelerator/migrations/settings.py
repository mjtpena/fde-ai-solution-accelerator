from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """Only what a migration run needs; never the API's identity-provider settings."""

    model_config = SettingsConfigDict(env_prefix="API_")

    database_url: PostgresDsn
    database_auth_mode: Literal["password", "managed_identity"] = "password"
    database_connect_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    # Extra CA bundle for verifying the server certificate (private CAs, test servers).
    database_tls_ca_file: str | None = Field(default=None, min_length=1)
    # Runtime roles granted least-privilege table access after an upgrade (optional).
    database_api_role: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    database_worker_role: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    # The PostgreSQL Entra administrator; may manage and read scope memberships.
    database_operator_role: str | None = Field(default=None, min_length=1, max_length=63)
