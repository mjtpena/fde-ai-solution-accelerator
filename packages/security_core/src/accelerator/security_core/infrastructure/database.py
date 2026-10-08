from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

SessionFactory = async_sessionmaker[AsyncSession]


class Base(DeclarativeBase):
    pass


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Share an injected engine without owning its credentials or lifecycle."""
    return async_sessionmaker(engine, expire_on_commit=False)
