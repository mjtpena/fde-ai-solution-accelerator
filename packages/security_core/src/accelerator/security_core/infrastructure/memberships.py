from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from accelerator.security_core.infrastructure.database import Base, SessionFactory


class ScopeMembership(Base):
    __tablename__ = "scope_memberships"

    object_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(255), primary_key=True)


class SqlAlchemyScopeMembershipRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def scope_ids_for(self, object_id: str) -> frozenset[str]:
        async with self._session_factory() as session:
            scopes = await session.scalars(
                select(ScopeMembership.scope_id).where(ScopeMembership.object_id == object_id)
            )
            return frozenset(scopes)
