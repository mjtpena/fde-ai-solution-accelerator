import asyncio

from alembic import context
from azure.identity.aio import DefaultAzureCredential
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.infrastructure.database import asyncpg_url, entra_password_provider
from accelerator.migrations.settings import MigrationSettings


def _run(connection: Connection) -> None:
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    settings = MigrationSettings()  # type: ignore[call-arg]  # values come from the environment
    connect_args: dict[str, object] = {"timeout": settings.database_connect_timeout_seconds}
    credential: DefaultAzureCredential | None = None
    if settings.database_auth_mode == "managed_identity":
        credential = DefaultAzureCredential()
        connect_args["password"] = entra_password_provider(credential)
        connect_args["ssl"] = "require"
    engine = create_async_engine(
        asyncpg_url(str(settings.database_url)),
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()
        if credential is not None:
            await credential.close()


if context.is_offline_mode():
    context.configure(url="postgresql://", literal_binds=True, dialect_name="postgresql")
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
