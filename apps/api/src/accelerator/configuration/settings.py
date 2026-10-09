from functools import lru_cache
from urllib.parse import urlsplit
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AliasChoices,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
DatabaseAuthMode = Literal["password", "managed_identity"]

# Environments where local fakes (offline workflow, unconfigured adapters) may stand
# in for Azure services. Every deployed environment, including an Azure "dev"
# environment, runs with ``environment=production`` and gets strict validation.
FAKE_FRIENDLY_ENVIRONMENTS: frozenset[str] = frozenset({"development", "test"})


class Settings(BaseSettings):
    # Validation errors must never echo inputs: a rejected DSN can carry a password.
    # Blank variables (e.g. compose pass-throughs of unset values) count as unset.
    model_config = SettingsConfigDict(
        env_prefix="API_", hide_input_in_errors=True, env_ignore_empty=True
    )

    environment: Environment
    request_token_budget: int = Field(default=8192, gt=0)
    request_rate_limit: int = Field(default=60, gt=0)
    request_rate_window_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_tool_calls_per_turn: int = Field(default=10, ge=1, le=100)
    max_tool_calls_per_session: int = Field(default=100, ge=1, le=10_000)
    diagnostics_include_content: bool = False
    # Unauthenticated requests write an audit row per 401. These caps bound that
    # write amplification; failures beyond them are counted in logs instead.
    auth_failure_audit_per_client_limit: int = Field(default=20, gt=0)
    auth_failure_audit_global_limit: int = Field(default=200, gt=0)
    auth_failure_audit_window_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    entra_tenant_id: UUID
    entra_audience: str = Field(min_length=1)
    # Sovereign clouds use a different authority, e.g. https://login.microsoftonline.us
    # or https://login.chinacloudapi.cn. Explicit issuer/JWKS URLs override it.
    entra_authority_host: HttpUrl = HttpUrl("https://login.microsoftonline.com")
    entra_issuer: str | None = Field(default=None, min_length=1)
    entra_jwks_uri: HttpUrl | None = None
    jwt_leeway_seconds: int = Field(default=60, ge=0, le=300)
    jwks_cache_seconds: float = Field(default=300.0, ge=30, le=86_400, allow_inf_nan=False)
    jwks_max_stale_seconds: float = Field(default=86_400.0, ge=60, le=7 * 86_400, allow_inf_nan=False)
    web_origin: str = Field(default="http://localhost:3000", min_length=1)

    # PostgreSQL. In production the DSN carries no password: the API authenticates
    # with a Microsoft Entra token from its managed identity.
    database_url: PostgresDsn | None = None
    database_auth_mode: DatabaseAuthMode = "password"
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=5, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    database_pool_recycle_seconds: int = Field(default=1800, gt=0)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    # Extra CA bundle for verifying the server certificate (private CAs, test servers).
    database_tls_ca_file: str | None = Field(default=None, min_length=1)

    # User-assigned managed identity used for every Azure dependency in production.
    # DefaultAzureCredential reads the same AZURE_CLIENT_ID variable.
    managed_identity_client_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices(
            "API_MANAGED_IDENTITY_CLIENT_ID", "AZURE_CLIENT_ID", "managed_identity_client_id"
        ),
    )

    # Microsoft Foundry project, chat-model and embedding-model deployments.
    foundry_project_endpoint: HttpUrl | None = None
    foundry_model_deployment: str | None = Field(default=None, min_length=1)
    foundry_embedding_deployment: str | None = Field(default=None, min_length=1)
    generation_max_output_tokens: int = Field(default=1024, ge=1, le=32_768)

    # Azure AI Search. Vector dimensions must match the embedding deployment and index.
    search_endpoint: HttpUrl | None = None
    search_index_name: str | None = Field(default=None, min_length=1)
    search_vector_dimensions: int | None = Field(default=None, ge=2, le=4096)
    search_semantic_ranking: bool = True
    search_vector_candidates: int = Field(default=50, ge=50, le=1000)
    search_top_k: int = Field(default=5, ge=1, le=20)

    # Evidence sufficiency gate. The default scale is the semantic reranker (0-4).
    sufficiency_score_field: Literal["score", "reranker_score"] = "reranker_score"
    sufficiency_min_score: float = Field(default=2.0, allow_inf_nan=False)
    sufficiency_min_evidence: int = Field(default=1, ge=1, le=20)

    # Application Insights. The standard Azure Monitor variable name is accepted
    # so platform-injected configuration works unchanged.
    applicationinsights_connection_string: SecretStr | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices(
            "API_APPLICATIONINSIGHTS_CONNECTION_STRING",
            "APPLICATIONINSIGHTS_CONNECTION_STRING",
            "applicationinsights_connection_string",
        ),
    )

    @field_validator("database_url")
    @classmethod
    def require_asyncpg_driver(cls, url: PostgresDsn | None) -> PostgresDsn | None:
        if url is not None and url.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
            raise ValueError("API_DATABASE_URL must use the asyncpg driver (postgresql+asyncpg).")
        return url

    @property
    def entra_issuer_url(self) -> str:
        if self.entra_issuer is not None:
            return self.entra_issuer
        return f"{str(self.entra_authority_host).rstrip('/')}/{self.entra_tenant_id}/v2.0"

    @property
    def entra_jwks_url(self) -> str:
        if self.entra_jwks_uri is not None:
            return str(self.entra_jwks_uri)
        host = str(self.entra_authority_host).rstrip("/")
        return f"{host}/{self.entra_tenant_id}/discovery/v2.0/keys"

    @property
    def allows_fakes(self) -> bool:
        return self.environment in FAKE_FRIENDLY_ENVIRONMENTS

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def require_consistent_retrieval(self) -> Self:
        """Combinations that would make every turn abstain fail in every environment."""
        if self.sufficiency_min_evidence > self.search_top_k:
            raise ValueError("API_SUFFICIENCY_MIN_EVIDENCE cannot exceed API_SEARCH_TOP_K.")
        if self.sufficiency_score_field == "reranker_score" and not self.search_semantic_ranking:
            raise ValueError(
                "API_SUFFICIENCY_SCORE_FIELD=reranker_score requires API_SEARCH_SEMANTIC_RANKING."
            )
        return self

    @model_validator(mode="after")
    def require_serialized_web_origin(self) -> Self:
        # CORS compares this string with the browser's Origin header, which is only
        # scheme://host[:port]; anything else would silently reject every request.
        try:
            origin = urlsplit(self.web_origin)
            port = origin.port
        except ValueError as exc:
            raise ValueError("API_WEB_ORIGIN must be scheme://host[:port].") from exc
        serialized = f"{origin.scheme}://{origin.hostname}" + (f":{port}" if port else "")
        if origin.scheme not in {"http", "https"} or not origin.hostname or self.web_origin != serialized:
            raise ValueError("API_WEB_ORIGIN must be scheme://host[:port], with nothing else.")
        return self

    @model_validator(mode="after")
    def require_consistent_jwks_cache(self) -> Self:
        # Keys past the stale limit are unusable; a cache that outlives them would
        # refuse every token until the cache timer finally triggers a refresh.
        if self.jwks_max_stale_seconds < self.jwks_cache_seconds:
            raise ValueError("API_JWKS_MAX_STALE_SECONDS cannot be less than API_JWKS_CACHE_SECONDS.")
        return self

    @model_validator(mode="after")
    def require_production_services(self) -> Self:
        if self.allows_fakes:
            return self
        missing = [
            name
            for name in (
                "managed_identity_client_id",
                "database_url",
                "foundry_project_endpoint",
                "foundry_model_deployment",
                "foundry_embedding_deployment",
                "search_endpoint",
                "search_index_name",
                "search_vector_dimensions",
                "applicationinsights_connection_string",
            )
            if getattr(self, name) is None
        ]
        if missing:
            variables = ", ".join(f"API_{name.upper()}" for name in missing)
            raise ValueError(f"Production requires these settings: {variables}.")
        if self.database_auth_mode != "managed_identity":
            raise ValueError("Production requires API_DATABASE_AUTH_MODE=managed_identity.")
        if "web_origin" not in self.model_fields_set:
            raise ValueError("Production requires API_WEB_ORIGIN to be set explicitly.")
        origin = urlsplit(self.web_origin)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.hostname in {"localhost", "127.0.0.1"}
            or "*" in self.web_origin
        ):
            raise ValueError("API_WEB_ORIGIN must be one public https origin in production.")
        if self.database_url is not None and self.database_url.hosts()[0].get("password"):
            raise ValueError("Production database URLs must not embed a password.")
        for name in (
            "foundry_project_endpoint",
            "search_endpoint",
            "entra_authority_host",
            "entra_jwks_uri",
        ):
            url: HttpUrl | None = getattr(self, name)
            if url is not None and url.scheme != "https":
                raise ValueError(f"API_{name.upper()} must use HTTPS in production.")
        return self

    @property
    def azure_services_configured(self) -> bool:
        return all(
            value is not None
            for value in (
                self.foundry_project_endpoint,
                self.foundry_model_deployment,
                self.foundry_embedding_deployment,
                self.search_endpoint,
                self.search_index_name,
                self.search_vector_dimensions,
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
