import os
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from accelerator.configuration.settings import Settings
from accelerator.security_core.content_safety import HarmCategory

TENANT = "00000000-0000-0000-0000-000000000001"


def production_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "environment": "production",
        "entra_tenant_id": TENANT,
        "entra_audience": "api://accelerator",
        "web_origin": "https://app.example.test",
        "database_url": "postgresql+asyncpg://api-identity@db.example.test:5432/accelerator",
        "database_auth_mode": "managed_identity",
        "managed_identity_client_id": "00000000-0000-0000-0000-0000000000c1",
        "foundry_project_endpoint": "https://foundry.example.test/api/projects/p",
        "foundry_model_deployment": "chat-model",
        "foundry_embedding_deployment": "embedding-model",
        "search_vector_dimensions": 1536,
        "search_endpoint": "https://search.example.test",
        "search_index_name": "chunks",
        "content_safety_endpoint": "https://safety.example.test",
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
        "managed_identity_client_id",
        "database_url",
        "foundry_project_endpoint",
        "foundry_model_deployment",
        "foundry_embedding_deployment",
        "search_endpoint",
        "search_index_name",
        "search_vector_dimensions",
        "applicationinsights_connection_string",
        "content_safety_endpoint",
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


def test_production_rejects_a_plain_http_jwks_override() -> None:
    with pytest.raises(ValidationError, match="API_ENTRA_JWKS_URI must use HTTPS"):
        Settings.model_validate(
            production_values(entra_jwks_uri="http://keys.example.test/discovery/keys")
        )


def test_jwks_stale_limit_cannot_be_shorter_than_the_cache() -> None:
    with pytest.raises(ValidationError, match="API_JWKS_MAX_STALE_SECONDS"):
        Settings(
            environment="test",
            entra_tenant_id=TENANT,
            entra_audience="api://accelerator",
            jwks_cache_seconds=600,
            jwks_max_stale_seconds=120,
        )


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


def test_rejected_database_password_never_appears_in_the_error() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings.model_validate(
            production_values(
                database_url="postgresql+asyncpg://user:hunter2-secret@db.example.test/accelerator"
            )
        )

    assert "hunter2-secret" not in str(raised.value)
    assert "hunter2-secret" not in repr(raised.value.errors(include_input=False))


@pytest.mark.parametrize(
    "variable",
    ["APPLICATIONINSIGHTS_CONNECTION_STRING", "API_APPLICATIONINSIGHTS_CONNECTION_STRING"],
)
def test_blank_application_insights_string_counts_as_missing_in_production(variable: str) -> None:
    environment = {
        f"API_{key.upper()}": str(value)
        for key, value in production_values().items()
        if key != "applicationinsights_connection_string"
    }
    environment[variable] = ""
    with patch.dict(os.environ, environment, clear=True), pytest.raises(
        ValidationError, match="APPLICATIONINSIGHTS_CONNECTION_STRING"
    ):
        Settings()  # type: ignore[call-arg]


def test_reranker_gate_requires_semantic_ranking() -> None:
    with pytest.raises(ValidationError, match="SEMANTIC_RANKING"):
        Settings.model_validate(production_values(search_semantic_ranking=False))

    settings = Settings.model_validate(
        production_values(search_semantic_ranking=False, sufficiency_score_field="score")
    )
    assert settings.azure_services_configured


def test_minimum_evidence_cannot_exceed_top_k_in_any_environment() -> None:
    with pytest.raises(ValidationError, match="MIN_EVIDENCE"):
        Settings.model_validate(
            {
                "environment": "development",
                "entra_tenant_id": TENANT,
                "entra_audience": "api://x",
                "search_top_k": 1,
                "sufficiency_min_evidence": 2,
            }
        )


def test_reranker_gate_rule_applies_outside_production_too() -> None:
    with pytest.raises(ValidationError, match="SEMANTIC_RANKING"):
        Settings.model_validate(
            {
                "environment": "development",
                "entra_tenant_id": TENANT,
                "entra_audience": "api://x",
                "search_semantic_ranking": False,
            }
        )


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "http://app.example.test",
        "https://localhost:3000",
        "https://*.example.test",
        "https://app.example.test/path",
    ],
)
def test_production_requires_one_explicit_public_https_web_origin(origin: str | None) -> None:
    values = production_values()
    if origin is None:
        del values["web_origin"]
    else:
        values["web_origin"] = origin

    with pytest.raises(ValidationError, match="API_WEB_ORIGIN"):
        Settings.model_validate(values)


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.example.test/",
        "https://app.example.test?x=1",
        "https://app.example.test#top",
        "https://user@app.example.test",
        "https://App.Example.test",
        "app.example.test",
        "https://app.example.test:notaport",
    ],
)
def test_web_origin_must_be_a_serialized_origin(origin: str) -> None:
    with pytest.raises(ValidationError, match="API_WEB_ORIGIN"):
        Settings.model_validate(production_values(web_origin=origin))
    with pytest.raises(ValidationError, match="API_WEB_ORIGIN"):
        Settings.model_validate(production_values(environment="test", web_origin=origin))


def test_web_origin_may_carry_a_port() -> None:
    settings = Settings.model_validate(production_values(web_origin="https://app.example.test:8443"))

    assert settings.web_origin == "https://app.example.test:8443"


def test_content_safety_is_on_by_default_and_cannot_be_disabled_in_production() -> None:
    assert Settings.model_validate(production_values()).content_safety_enabled
    with pytest.raises(ValidationError, match="API_CONTENT_SAFETY_ENABLED=true"):
        Settings.model_validate(production_values(content_safety_enabled=False))


def test_production_content_safety_endpoint_must_use_https() -> None:
    with pytest.raises(ValidationError, match="API_CONTENT_SAFETY_ENDPOINT must use HTTPS"):
        Settings.model_validate(
            production_values(content_safety_endpoint="http://safety.example.test")
        )


def test_development_may_disable_content_safety() -> None:
    settings = Settings.model_validate(
        {
            "environment": "development",
            "entra_tenant_id": TENANT,
            "entra_audience": "api://x",
            "content_safety_enabled": False,
        }
    )

    assert not settings.content_safety_enabled


def test_content_safety_thresholds_default_to_medium_and_are_per_category() -> None:
    settings = Settings.model_validate(
        production_values(content_safety_block_severity_self_harm=2)
    )

    assert settings.content_safety_policy.thresholds == {
        HarmCategory.HATE: 4,
        HarmCategory.SELF_HARM: 2,
        HarmCategory.SEXUAL: 4,
        HarmCategory.VIOLENCE: 4,
    }


@pytest.mark.parametrize("threshold", [0, 7])
def test_content_safety_thresholds_must_be_able_to_block(threshold: int) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            production_values(content_safety_block_severity_violence=threshold)
        )


def test_streaming_is_screened_by_default_and_required_in_production() -> None:
    settings = Settings.model_validate(production_values())

    assert settings.stream_release_mode == "screened"
    with pytest.raises(ValidationError, match="API_STREAM_RELEASE_MODE=screened"):
        Settings.model_validate(production_values(stream_release_mode="incremental"))


def test_development_may_stream_incrementally() -> None:
    settings = Settings.model_validate(
        {
            "environment": "development",
            "entra_tenant_id": TENANT,
            "entra_audience": "api://x",
            "stream_release_mode": "incremental",
        }
    )

    assert settings.stream_release_mode == "incremental"


@pytest.mark.parametrize("seconds", [0, 10.5])
def test_stream_heartbeat_stays_inside_the_web_proxy_timeouts(seconds: float) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(production_values(stream_heartbeat_seconds=seconds))
