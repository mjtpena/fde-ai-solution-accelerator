import asyncio
import os
from typing import Any
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from accelerator.api import health
from accelerator.api.app import create_app
from accelerator.configuration.settings import Settings
from accelerator.identity.errors import AuthProviderUnavailable
from accelerator.security_core.data_boundaries.context import ExecutionContext


def make_test_settings() -> Settings:
    return Settings(
        environment="test",
        entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        entra_audience="api://test",
    )


class KeysAvailable:
    async def ensure_signing_keys(self, client: httpx.AsyncClient) -> None:
        del client


class KeysUnavailable:
    async def ensure_signing_keys(self, client: httpx.AsyncClient) -> None:
        del client
        raise AuthProviderUnavailable


class ConfiguredChat:
    async def run(self, query: str, ctx: ExecutionContext) -> Any:
        raise AssertionError("readiness must not run the workflow")


def sqlite_sessions() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(create_async_engine("sqlite+aiosqlite://"))


def ready_app(**overrides: Any) -> FastAPI:
    app = create_app(
        make_test_settings(),
        chat_turn=overrides.pop("chat_turn", ConfiguredChat()),
        session_factory=overrides.pop("session_factory", sqlite_sessions()),
    )
    app.state.token_validator = overrides.pop("token_validator", KeysAvailable())
    assert not overrides
    return app


async def get(app: FastAPI, path: str) -> httpx.Response:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(path)


async def test_healthz_is_unauthenticated_liveness() -> None:
    response = await get(create_app(make_test_settings()), "/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_is_ready_when_every_dependency_is() -> None:
    response = await get(ready_app(), "/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": {"status": "ok", "reason": None},
            "identity_provider": {"status": "ok", "reason": None},
            "chat_workflow": {"status": "ok", "reason": None},
        },
    }


async def test_readyz_reports_missing_database_configuration() -> None:
    app = ready_app()
    app.state.session_factory = None

    response = await get(app, "/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database"]["status"] == "not_configured"


async def test_readyz_reports_unreachable_database_without_leaking_details() -> None:
    unreachable = async_sessionmaker(
        create_async_engine(
            "postgresql+asyncpg://user:secret-password@127.0.0.1:1/db",
            connect_args={"timeout": 1},
        )
    )

    response = await get(ready_app(session_factory=unreachable), "/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {
        "status": "failed",
        "reason": "Dependency check failed.",
    }
    assert "secret-password" not in response.text
    assert "127.0.0.1" not in response.text


async def test_readyz_reports_unavailable_signing_keys() -> None:
    response = await get(ready_app(token_validator=KeysUnavailable()), "/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["identity_provider"]["status"] == "failed"


async def test_readyz_reports_unconfigured_chat_workflow() -> None:
    app = ready_app()
    del app.state.chat_turn

    response = await get(app, "/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["chat_workflow"]["status"] == "not_configured"


async def test_readyz_times_out_a_hanging_dependency() -> None:
    class HangingValidator:
        async def ensure_signing_keys(self, client: httpx.AsyncClient) -> None:
            await asyncio.sleep(60)

    with patch.object(health, "READINESS_CHECK_TIMEOUT_SECONDS", 0.05):
        response = await get(ready_app(token_validator=HangingValidator()), "/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["identity_provider"] == {
        "status": "failed",
        "reason": "Check timed out.",
    }


def test_readiness_contract_is_documented_in_openapi() -> None:
    operation = create_app(make_test_settings()).openapi()["paths"]["/readyz"]["get"]

    assert {"200", "503"} <= set(operation["responses"])
    assert "401" not in operation["responses"]


class TestSettings:
    def test_missing_environment_fails_validation(self) -> None:
        with patch.dict(os.environ, {}, clear=True), pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_empty_environment_fails_validation(self) -> None:
        with patch.dict(os.environ, {"API_ENVIRONMENT": ""}, clear=True), pytest.raises(
            ValidationError
        ):
            Settings()  # type: ignore[call-arg]
