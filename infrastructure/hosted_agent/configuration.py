"""Fail-fast settings for the hosted runtime and data-plane deployment."""

import re
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        }
