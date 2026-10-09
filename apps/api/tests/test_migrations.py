"""Migrations must produce exactly the schema the ORM adapters expect."""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.infrastructure import audit as _audit  # noqa: F401  registers audit_event
from accelerator.infrastructure import cost_controls as _cost_controls  # noqa: F401
from accelerator.infrastructure.approvals import ApprovalBase
from accelerator.ingestion.infrastructure.repository import metadata as ingestion_metadata
from accelerator.migrations import alembic_config
from accelerator.security_core.infrastructure.database import Base
from accelerator.security_core.infrastructure.memberships import (  # noqa: F401
    ScopeMembership,
)


def test_migration_history_is_linear() -> None:
    script = ScriptDirectory.from_config(alembic_config())

    assert len(script.get_heads()) == 1
    assert [revision.revision for revision in script.walk_revisions()] == [
        "0006_authorization_failure_audit",
        "0005_ingestion",
        "0004_cost_controls",
        "0003_approvals",
        "0002_scope_memberships",
        "0001_audit_event",
    ]


async def _schema_drift(url: str) -> list[object]:
    metadata = MetaData()
    for source in (Base.metadata, ApprovalBase.metadata, ingestion_metadata):
        for table in source.tables.values():
            table.to_metadata(metadata)
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), metadata)
            )
    finally:
        await engine.dispose()


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    finally:
        await engine.dispose()


async def _append_only_rejects_update(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text("DELETE FROM audit_event"))
    finally:
        await engine.dispose()


def test_upgrade_matches_orm_metadata_and_downgrade_is_clean(empty_database_url: str) -> None:
    config = alembic_config()
    with patch.dict(
        os.environ,
        # Throwaway databases use the DSN's password, whatever the shell exports.
        {"API_DATABASE_URL": empty_database_url, "API_DATABASE_AUTH_MODE": "password"},
    ):
        command.upgrade(config, "head")
        assert asyncio.run(_schema_drift(empty_database_url)) == []
        asyncio.run(_append_only_rejects_update(empty_database_url))
        command.downgrade(config, "base")

    assert asyncio.run(_table_names(empty_database_url)) == {"alembic_version"}


async def _apply_legacy_schema(url: str) -> None:
    sql = (Path(__file__).parent / "fixtures" / "legacy_001_audit_event.sql").read_text()
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            driver = await connection.get_raw_connection()
            await driver.driver_connection.execute(sql)  # type: ignore[union-attr]
    finally:
        await engine.dispose()


def test_databases_built_from_the_legacy_sql_file_adopt_alembic(empty_database_url: str) -> None:
    asyncio.run(_apply_legacy_schema(empty_database_url))
    with patch.dict(
        os.environ,
        {"API_DATABASE_URL": empty_database_url, "API_DATABASE_AUTH_MODE": "password"},
    ):
        command.upgrade(alembic_config(), "head")

    assert asyncio.run(_schema_drift(empty_database_url)) == []
    asyncio.run(_append_only_rejects_update(empty_database_url))


async def _create_roles(url: str, *roles: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for role in roles:
                await connection.execute(
                    text(
                        f"DO $$ BEGIN CREATE ROLE {role}; "
                        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
                    )
                )
    finally:
        await engine.dispose()


async def _privileges(url: str, role: str) -> dict[str, set[str]]:
    actions = ("SELECT", "INSERT", "UPDATE", "DELETE")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync: inspect(sync).get_table_names()
            )
            result: dict[str, set[str]] = {}
            for table in tables:
                granted = {
                    action
                    for action in actions
                    if (
                        await connection.execute(
                            text("SELECT has_table_privilege(:role, :table, :action)"),
                            {"role": role, "table": table, "action": action},
                        )
                    ).scalar_one()
                }
                if granted:
                    result[table] = granted
            return result
    finally:
        await engine.dispose()


def test_upgrade_grants_runtime_roles_only_what_their_code_uses(
    empty_database_url: str,
) -> None:
    suffix = os.urandom(4).hex()
    api_role, worker_role = f"test_api_{suffix}", f"test_worker_{suffix}"
    operator_role = f"DB Admins {suffix}"
    asyncio.run(_create_roles(empty_database_url, api_role, worker_role, f'"{operator_role}"'))
    with patch.dict(
        os.environ,
        {
            "API_DATABASE_URL": empty_database_url,
            "API_DATABASE_AUTH_MODE": "password",
            "API_DATABASE_API_ROLE": api_role,
            "API_DATABASE_WORKER_ROLE": worker_role,
            "API_DATABASE_OPERATOR_ROLE": operator_role,
        },
    ):
        command.upgrade(alembic_config(), "head")

    api = asyncio.run(_privileges(empty_database_url, api_role))
    operator = asyncio.run(_privileges(empty_database_url, operator_role))
    assert operator == {"scope_memberships": {"SELECT", "INSERT", "DELETE"}}
    worker = asyncio.run(_privileges(empty_database_url, worker_role))

    assert api["audit_event"] == {"SELECT", "INSERT"}  # append-only
    assert api["approval_audit_events"] == {"SELECT", "INSERT"}
    assert api["scope_memberships"] == {"SELECT"}
    assert "documents" not in api and "alembic_version" not in api
    assert worker == {
        "documents": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "document_chunks": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    }


def test_runtime_role_names_must_be_plain_identifiers() -> None:
    from accelerator.migrations.grants import quoted_role

    assert quoted_role("accelerator_api") == '"accelerator_api"'
    for name in ("Api", 'x"; DROP TABLE audit_event; --', "", "a-b"):
        with pytest.raises(ValueError):
            quoted_role(name)
    # Entra administrator names are quoted, with embedded quotes doubled.
    assert quoted_role('DB "Admins"', entra=True) == '"DB ""Admins"""'
    with pytest.raises(ValueError):
        quoted_role("bad\nname", entra=True)


class _PasswordAsToken:
    """Stands in for DefaultAzureCredential: the test server's password is the token."""

    password = ""

    async def get_token(self, *scopes: str, **kwargs: object) -> object:
        from azure.core.credentials import AccessToken

        return AccessToken(self.password, 4_102_444_800)

    async def close(self) -> None:
        return None


def test_managed_identity_migrations_send_the_token_only_over_verified_tls(
    empty_database_url: str,
) -> None:
    import ssl

    from sqlalchemy.engine import make_url

    ca_file = os.environ.get("TEST_POSTGRES_CA_FILE")
    if not ca_file:
        pytest.skip("Set TEST_POSTGRES_CA_FILE to the test server's certificate.")
    url = make_url(empty_database_url)
    _PasswordAsToken.password = url.password or ""
    environment = {
        "API_DATABASE_URL": url.set(password=None).render_as_string(hide_password=False),
        "API_DATABASE_AUTH_MODE": "managed_identity",
    }
    with patch("azure.identity.aio.DefaultAzureCredential", _PasswordAsToken):
        # Without the test CA the self-signed server certificate is rejected.
        with patch.dict(os.environ, environment), pytest.raises(ssl.SSLCertVerificationError):
            command.upgrade(alembic_config(), "head")
        with patch.dict(os.environ, {**environment, "API_DATABASE_TLS_CA_FILE": ca_file}):
            command.upgrade(alembic_config(), "head")

    assert "audit_event" in asyncio.run(_table_names(empty_database_url))
