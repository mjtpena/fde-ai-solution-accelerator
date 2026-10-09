"""PostgreSQL engine construction for the API host."""

from collections.abc import Awaitable, Callable

from azure.core.credentials_async import AsyncTokenCredential
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from accelerator.configuration.settings import Settings

# Microsoft Entra resource for Azure Database for PostgreSQL Flexible Server.
POSTGRES_ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def entra_password_provider(
    credential: AsyncTokenCredential,
) -> Callable[[], Awaitable[str]]:
    """Return an asyncpg password callable that fetches a fresh Entra token per connection."""

    async def password() -> str:
        token = await credential.get_token(POSTGRES_ENTRA_SCOPE)
        return token.token

    return password


def asyncpg_url(url: str) -> str:
    """Pin the asyncpg driver so a plain ``postgresql://`` DSN works unchanged."""
    scheme, separator, rest = url.partition("://")
    if scheme in {"postgres", "postgresql"}:
        return f"postgresql+asyncpg{separator}{rest}"
    return url


def create_database_engine(
    settings: Settings,
    *,
    credential: AsyncTokenCredential | None = None,
) -> AsyncEngine:
    """Build the async engine with explicit pool limits.

    No connection is opened here. Managed-identity mode requires ``credential`` and
    authenticates every new pooled connection with a short-lived Entra token, so the
    pool recycle interval stays below the token lifetime.
    """
    if settings.database_url is None:
        raise ValueError("API_DATABASE_URL is not configured.")
    connect_args: dict[str, object] = {"timeout": settings.database_connect_timeout_seconds}
    if settings.database_auth_mode == "managed_identity":
        if credential is None:
            raise ValueError("Managed-identity database auth requires an Azure credential.")
        connect_args["password"] = entra_password_provider(credential)
        connect_args["ssl"] = "require"
    return create_async_engine(
        asyncpg_url(str(settings.database_url)),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
