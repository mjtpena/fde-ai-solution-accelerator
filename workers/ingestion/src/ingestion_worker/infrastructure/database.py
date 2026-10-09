from azure.core.credentials_async import AsyncTokenCredential
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

POSTGRES_ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def create_engine(
    url: str, *, managed_identity: bool, credential: AsyncTokenCredential | None
) -> AsyncEngine:
    scheme, separator, rest = url.partition("://")
    if scheme in {"postgres", "postgresql"}:
        url = f"postgresql+asyncpg{separator}{rest}"
    connect_args: dict[str, object] = {"timeout": 10}
    if managed_identity:
        if credential is None:
            raise ValueError("Managed-identity database auth requires an Azure credential.")

        async def password() -> str:
            return (await credential.get_token(POSTGRES_ENTRA_SCOPE)).token

        connect_args.update(password=password, ssl="require")
    return create_async_engine(
        url, pool_size=2, max_overflow=2, pool_pre_ping=True, pool_recycle=1800,
        connect_args=connect_args,
    )
