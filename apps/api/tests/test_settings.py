import os
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from accelerator.configuration.settings import Settings

TENANT = "00000000-0000-0000-0000-000000000001"


def production_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "environment": "production",
        "entra_tenant_id": TENANT,
        "entra_audience": "api://accelerator",
        "web_origin": "https://app.example.test",
        "database_url": "postgresql+asyncpg://api-identity@db.example.test:5432/accelerator",
        "database_auth_mode": "managed_identity",
        "foundry_project_endpoint": "https://foundry.example.test/api/projects/p",
        "foundry_model_deployment": "chat-model",
        "search_endpoint": "https://search.example.test",
        "search_index_name": "chunks",
        "applicationinsights_connection_string": "InstrumentationKey=test",
    }
    values.update(overrides)
    return values


def test_development_allows_missing_azure_services() -> None:
    settings = Settings.model_validate(
        {"environment": "development", "entra_tenant_id": TENANT, "entra_audience": "api://x"}
    )

    assert settings.allows_fakes
    assert settings.database_url is None
    assert settings.search_endpoint is None


def test_production_accepts_complete_managed_identity_configuration() -> None:
    settings = Settings.model_validate(production_values())

    assert settings.is_production
    assert not settings.allows_fakes
    assert settings.database_auth_mode == "managed_identity"


@pytest.mark.parametrize(
    "missing",
    [
        "database_url",
        "foundry_project_endpoint",
        "foundry_model_deployment",
        "search_endpoint",
        "search_index_name",
        "applicationinsights_connection_string",
    ],
)
def test_production_fails_fast_on_each_missing_service(missing: str) -> None:
    with pytest.raises(ValidationError, match=f"API_{missing.upper()}"):
        Settings.model_validate(production_values(**{missing: None}))


def test_production_rejects_password_database_auth() -> None:
    with pytest.raises(ValidationError, match="managed_identity"):
        Settings.model_validate(production_values(database_auth_mode="password"))


def test_production_rejects_database_url_with_embedded_password() -> None:
    with pytest.raises(ValidationError, match="must not embed a password"):
        Settings.model_validate(
            production_values(
                database_url="postgresql+asyncpg://user:secret@db.example.test/accelerator"
            )
        )


def test_production_rejects_plain_http_azure_endpoints() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings.model_validate(production_values(search_endpoint="http://search.example.test"))


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {"environment": "staging", "entra_tenant_id": TENANT, "entra_audience": "api://x"}
        )


def test_reads_standard_application_insights_variable() -> None:
    environment = {
        "API_ENVIRONMENT": "development",
        "API_ENTRA_TENANT_ID": TENANT,
        "API_ENTRA_AUDIENCE": "api://x",
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=from-platform",
    }
    with patch.dict(os.environ, environment, clear=True):
        settings = Settings()  # type: ignore[call-arg]

    assert settings.applicationinsights_connection_string is not None
    assert (
        settings.applicationinsights_connection_string.get_secret_value()
        == "InstrumentationKey=from-platform"
    )
    assert "from-platform" not in repr(settings)
