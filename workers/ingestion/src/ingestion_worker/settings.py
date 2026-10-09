from typing import Literal, Self

from pydantic import Field, HttpUrl, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    # Validation errors must never echo inputs: a rejected DSN can carry a password.
    model_config = SettingsConfigDict(
        env_prefix="INGESTION_", env_ignore_empty=True, hide_input_in_errors=True
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # Storage: account endpoints with managed identity in production. A connection
    # string is accepted only outside production, for the Azurite emulator.
    blob_account_url: HttpUrl | None = None
    queue_account_url: HttpUrl | None = None
    storage_connection_string: SecretStr | None = None
    queue_name: str = Field(default="ingestion", pattern=r"^[a-z0-9-]{3,63}$")
    poison_queue_name: str = Field(default="ingestion-poison", pattern=r"^[a-z0-9-]{3,63}$")
    incoming_container: str = Field(default="incoming", pattern=r"^[a-z0-9-]{3,63}$")
    documents_container: str = Field(default="documents", pattern=r"^[a-z0-9-]{3,63}$")

    database_url: PostgresDsn | None = None
    database_auth_mode: Literal["password", "managed_identity"] = "password"
    # Extra CA bundle for verifying the server certificate (private CAs, test servers).
    database_tls_ca_file: str | None = Field(default=None, min_length=1)

    search_endpoint: HttpUrl | None = None
    search_index_name: str | None = None
    vector_dimensions: int | None = Field(default=None, ge=2, le=4096)
    foundry_project_endpoint: HttpUrl | None = None
    foundry_embedding_deployment: str | None = None
    managed_identity_client_id: str | None = None

    max_document_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    chunk_size: int = Field(default=1200, ge=100)
    chunk_overlap: int = Field(default=150, ge=0)
    embedding_batch_size: int = Field(default=16, ge=1, le=256)
    max_attempts: int = Field(default=5, ge=1, le=50)
    visibility_timeout_seconds: int = Field(default=300, ge=30, le=7 * 24 * 3600)

    @property
    def indexing_configured(self) -> bool:
        return all(
            value is not None
            for value in (
                self.database_url,
                self.search_endpoint,
                self.search_index_name,
                self.vector_dimensions,
                self.foundry_project_endpoint,
                self.foundry_embedding_deployment,
            )
        ) and (
            self.storage_connection_string is not None
            or (self.blob_account_url is not None and self.queue_account_url is not None)
        )

    @model_validator(mode="after")
    def queues_are_distinct(self) -> Self:
        if self.queue_name == self.poison_queue_name:
            # Poisoned wrappers would land back on the work queue and loop forever.
            raise ValueError("INGESTION_POISON_QUEUE_NAME must differ from INGESTION_QUEUE_NAME.")
        return self

    @model_validator(mode="after")
    def production_uses_managed_identity(self) -> Self:
        if self.environment != "production":
            return self
        if self.storage_connection_string is not None:
            raise ValueError(
                "Production storage access uses managed identity, not a connection string."
            )
        if self.database_auth_mode != "managed_identity":
            raise ValueError("Production requires INGESTION_DATABASE_AUTH_MODE=managed_identity.")
        if not self.indexing_configured:
            raise ValueError(
                "Production requires storage, database, search and embedding settings."
            )
        if self.database_url is not None and self.database_url.hosts()[0].get("password"):
            raise ValueError(
                "Production database access uses managed identity; remove the DSN password."
            )
        endpoints = (
            self.blob_account_url,
            self.queue_account_url,
            self.search_endpoint,
            self.foundry_project_endpoint,
        )
        if any(endpoint is not None and endpoint.scheme != "https" for endpoint in endpoints):
            raise ValueError("Production Azure endpoints must use https.")
        return self
