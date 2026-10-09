"""Shared PostgreSQL fixtures.

Tests that need a real database request ``migrated_database_url``. They are
collected everywhere and skipped unless TEST_POSTGRES_DSN names a server where
the role can create databases; CI always sets it. Each test gets a throwaway
database migrated with the production Alembic history, so triggers and
constraints match deployed environments.
"""

import asyncio
import os
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.infrastructure.database import asyncpg_url
from accelerator.migrations import alembic_config

POSTGRES_DSN_VARIABLE = "TEST_POSTGRES_DSN"


async def _execute_autocommit(dsn: str, statement: str) -> None:
    engine = create_async_engine(asyncpg_url(dsn), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.environ.get(POSTGRES_DSN_VARIABLE)
    if not dsn:
        pytest.skip(f"Set {POSTGRES_DSN_VARIABLE} to run PostgreSQL-backed tests.")
    return dsn


@pytest.fixture
def empty_database_url(postgres_dsn: str) -> Iterator[str]:
    name = f"accelerator_test_{uuid4().hex}"
    asyncio.run(_execute_autocommit(postgres_dsn, f'CREATE DATABASE "{name}"'))
    url = make_url(asyncpg_url(postgres_dsn)).set(database=name)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        asyncio.run(_execute_autocommit(postgres_dsn, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
def migrated_database_url(empty_database_url: str) -> str:
    with patch.dict(
        os.environ,
        # Throwaway databases use the DSN's password, whatever the shell exports.
        {"API_DATABASE_URL": empty_database_url, "API_DATABASE_AUTH_MODE": "password"},
    ):
        command.upgrade(alembic_config(), "head")
    return empty_database_url
