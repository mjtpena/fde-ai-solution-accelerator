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
from accelerator.infrastructure.approvals import ApprovalBase
from accelerator.migrations import alembic_config
from accelerator.security_core.infrastructure.database import Base
from accelerator.security_core.infrastructure.memberships import (  # noqa: F401
    ScopeMembership,
)


def test_migration_history_is_linear() -> None:
    script = ScriptDirectory.from_config(alembic_config())

    assert len(script.get_heads()) == 1
    assert [revision.revision for revision in script.walk_revisions()] == [
        "0003_approvals",
        "0002_scope_memberships",
        "0001_audit_event",
    ]


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
