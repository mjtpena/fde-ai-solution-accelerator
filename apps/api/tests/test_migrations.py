"""Migrations must produce exactly the schema the ORM adapters expect.

Runs when TEST_POSTGRES_DSN points at a PostgreSQL server where the role may
create databases (CI provides one). Each run uses a throwaway database.
"""

import asyncio
import os
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.infrastructure import audit as _audit  # noqa: F401  registers audit_event
from accelerator.infrastructure.approvals import ApprovalBase
from accelerator.infrastructure.database import asyncpg_url
from accelerator.security_core.infrastructure.database import Base
from accelerator.security_core.infrastructure.memberships import (  # noqa: F401
    ScopeMembership,
)

POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_DSN, reason="Set TEST_POSTGRES_DSN to run migration tests."
)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("accelerator.migrations")))
    return config


def test_migration_history_is_linear() -> None:
    script = ScriptDirectory.from_config(alembic_config())

    assert len(script.get_heads()) == 1
    assert [revision.revision for revision in script.walk_revisions()] == [
        "0003_approvals",
        "0002_scope_memberships",
        "0001_audit_event",
    ]


async def _execute_admin(statement: str) -> None:
    assert POSTGRES_DSN is not None
    engine = create_async_engine(asyncpg_url(POSTGRES_DSN), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture
def migration_database() -> Iterator[str]:
    assert POSTGRES_DSN is not None
    name = f"migration_test_{uuid4().hex}"
    asyncio.run(_execute_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(asyncpg_url(POSTGRES_DSN)).set(database=name)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        asyncio.run(_execute_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


async def _schema_drift(url: str) -> list[object]:
    metadata = MetaData()
    for source in (Base.metadata, ApprovalBase.metadata):
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
            with pytest.raises(Exception, match="append-only"):
                await connection.execute(text("DELETE FROM audit_event"))
    finally:
        await engine.dispose()


@requires_postgres
def test_upgrade_matches_orm_metadata_and_downgrade_is_clean(migration_database: str) -> None:
    config = alembic_config()
    with patch.dict(os.environ, {"API_DATABASE_URL": migration_database}):
        command.upgrade(config, "head")
        assert asyncio.run(_schema_drift(migration_database)) == []
        asyncio.run(_append_only_rejects_update(migration_database))
        command.downgrade(config, "base")

    assert asyncio.run(_table_names(migration_database)) == {"alembic_version"}


async def _apply_legacy_schema(url: str) -> None:
    sql = (Path(__file__).parent / "fixtures" / "legacy_001_audit_event.sql").read_text()
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            driver = await connection.get_raw_connection()
            await driver.driver_connection.execute(sql)  # type: ignore[union-attr]
    finally:
        await engine.dispose()


@requires_postgres
def test_databases_built_from_the_legacy_sql_file_adopt_alembic(migration_database: str) -> None:
    asyncio.run(_apply_legacy_schema(migration_database))
    with patch.dict(
        os.environ,
        {"API_DATABASE_URL": migration_database, "API_DATABASE_AUTH_MODE": "password"},
    ):
        command.upgrade(alembic_config(), "head")

    assert asyncio.run(_schema_drift(migration_database)) == []
    asyncio.run(_append_only_rejects_update(migration_database))
