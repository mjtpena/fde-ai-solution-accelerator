"""Fail-fast settings for the hosted runtime and data-plane deployment."""

import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from accelerator.security_core.content_safety import (
    DEFAULT_BLOCK_SEVERITY,
    ContentSafetyPolicy,
    HarmCategory,
)


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOSTED_", extra="forbid", hide_input_in_errors=True
    )

    application_factory: str = Field(
        default="infrastructure.hosted_agent.production:runtime_factory",
        pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$",
    )
    context_resolver_factory: str = Field(pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$")
    grounded_workflow_factory: str = Field(pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$")


def _require_https_endpoint(value: str, setting: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.query
        or url.fragment
    ):
        raise ValueError(f"{setting} must be an HTTPS endpoint without credentials or query")
    return value


class ContentSafetySettings(BaseSettings):
    """Azure AI Content Safety for the hosted runtime (ADR-0007).

    Same names and semantics as the API's ``API_CONTENT_SAFETY_*`` settings, with the
    ``HOSTED_`` prefix. The hosted runtime is a production path: the endpoint is
    required, HTTPS only, and screening cannot be disabled.
    """

    model_config = SettingsConfigDict(
        env_prefix="HOSTED_", extra="forbid", hide_input_in_errors=True
    )

    content_safety_enabled: bool = True
    content_safety_endpoint: str
    content_safety_timeout_seconds: float = Field(default=5.0, gt=0, le=60, allow_inf_nan=False)
    content_safety_block_severity_hate: int = Field(default=DEFAULT_BLOCK_SEVERITY, ge=1, le=6)
    content_safety_block_severity_self_harm: int = Field(
        default=DEFAULT_BLOCK_SEVERITY, ge=1, le=6
    )
    content_safety_block_severity_sexual: int = Field(default=DEFAULT_BLOCK_SEVERITY, ge=1, le=6)
    content_safety_block_severity_violence: int = Field(
        default=DEFAULT_BLOCK_SEVERITY, ge=1, le=6
    )
    # A user-assigned identity's client ID; unset uses the platform-assigned identity.
    managed_identity_client_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices(
            "HOSTED_MANAGED_IDENTITY_CLIENT_ID", "AZURE_CLIENT_ID", "managed_identity_client_id"
        ),
    )

    @field_validator("content_safety_endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _require_https_endpoint(value, "HOSTED_CONTENT_SAFETY_ENDPOINT")

    @model_validator(mode="after")
    def require_screening(self) -> Self:
        if not self.content_safety_enabled:
            raise ValueError("The hosted agent requires HOSTED_CONTENT_SAFETY_ENABLED=true.")
        return self

    @property
    def content_safety_policy(self) -> ContentSafetyPolicy:
        return ContentSafetyPolicy(
            {
                HarmCategory.HATE: self.content_safety_block_severity_hate,
                HarmCategory.SELF_HARM: self.content_safety_block_severity_self_harm,
                HarmCategory.SEXUAL: self.content_safety_block_severity_sexual,
                HarmCategory.VIOLENCE: self.content_safety_block_severity_violence,
            }
        )


class DeploymentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOSTED_", extra="forbid", hide_input_in_errors=True
    )

    project_endpoint: str
    agent_name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")
    image: str
    application_factory: str = Field(
        default="infrastructure.hosted_agent.production:runtime_factory",
        pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$",
    )
    context_resolver_factory: str = Field(pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$")
    grounded_workflow_factory: str = Field(pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$")
    model_deployment: str = Field(min_length=1)
    # Main deployment output ``contentSafetyEndpoint``; the runtime refuses to start
    # without it (ADR-0007).
    content_safety_endpoint: str
    cpu: Literal["0.5", "1", "2", "4"] = "1"
    memory: Literal["1Gi", "2Gi", "4Gi", "8Gi"] = "2Gi"
    timeout_seconds: float = Field(default=300, gt=0, le=1800)
    poll_seconds: float = Field(default=5, gt=0, le=60)
    credential_mode: Literal["managed-identity", "azure-cli"] = "managed-identity"
    managed_identity_client_id: str | None = None

    @field_validator("project_endpoint")
    @classmethod
    def validate_project_endpoint(cls, value: str) -> str:
        if not re.fullmatch(
            r"https://[a-zA-Z0-9-]+\.services\.ai\.azure\.com/api/projects/[a-zA-Z0-9_.-]+",
            value,
        ):
            raise ValueError("Expected an HTTPS Foundry project endpoint without query/credentials")
        return value

    @field_validator("content_safety_endpoint")
    @classmethod
    def validate_content_safety_endpoint(cls, value: str) -> str:
        return _require_https_endpoint(value, "HOSTED_CONTENT_SAFETY_ENDPOINT")

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        if (
            not re.fullmatch(
                r"[a-z0-9]+\.azurecr\.io/[a-z0-9][a-z0-9/_.-]*"
                r"(?:@sha256:[a-f0-9]{64}|:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127})",
                value,
            )
            or value.rsplit(":", 1)[-1].lower() == "latest"
        ):
            raise ValueError("Expected an ACR image with an immutable tag or sha256 digest")
        return value

    def runtime_environment(self) -> dict[str, str]:
        return {
            "HOSTED_APPLICATION_FACTORY": self.application_factory,
            "HOSTED_CONTEXT_RESOLVER_FACTORY": self.context_resolver_factory,
            "HOSTED_GROUNDED_WORKFLOW_FACTORY": self.grounded_workflow_factory,
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": self.model_deployment,
            "HOSTED_CONTENT_SAFETY_ENDPOINT": self.content_safety_endpoint,
        }
