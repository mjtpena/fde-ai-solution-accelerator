from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Uuid, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from accelerator.agent_core.approvals import Approval, ApprovalAuditEvent


class ApprovalBase(DeclarativeBase):
    pass


class ApprovalRecord(ApprovalBase):
    __tablename__ = "approvals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)

    def to_approval(self) -> Approval:
        return Approval(
            id=self.id,
            tool_name=self.tool_name,
            args_hash=self.args_hash,
            scope_id=self.scope_id,
            requested_by=self.requested_by,
            status=self.status,  # validated by the Pydantic model
            decided_by=self.decided_by,
            expires_at=self.expires_at,
            correlation_id=self.correlation_id,
        )


class ApprovalAuditRecord(ApprovalBase):
    __tablename__ = "approval_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    approval_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id"), nullable=False
    )
    transition: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)


class SQLAlchemyApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        if self._session.in_transaction():
            async with self._session.begin_nested():
                yield
        else:
            async with self._session.begin():
                yield

    async def get_for_update(self, approval_id: UUID) -> Approval | None:
        statement = (
            select(ApprovalRecord)
            .where(ApprovalRecord.id == approval_id)
            .with_for_update()
        )
        result = await self._session.execute(statement)
        record = result.scalar_one_or_none()
        return None if record is None else record.to_approval()

    async def add(self, approval: Approval) -> None:
        self._session.add(
            ApprovalRecord(
                id=approval.id,
                tool_name=approval.tool_name,
                args_hash=approval.args_hash,
                scope_id=approval.scope_id,
                requested_by=approval.requested_by,
                status=approval.status,
                decided_by=approval.decided_by,
                expires_at=approval.expires_at,
                correlation_id=approval.correlation_id,
            )
        )

    async def update(self, approval: Approval) -> None:
        statement = (
            update(ApprovalRecord)
            .where(ApprovalRecord.id == approval.id)
            .values(
                status=approval.status,
                decided_by=approval.decided_by,
                expires_at=approval.expires_at,
            )
        )
        result = await self._session.execute(statement)
        if not isinstance(result, CursorResult):
            raise RuntimeError("approval update did not return a database cursor result")
        if result.rowcount != 1:
            raise LookupError(f"approval {approval.id} disappeared during its transaction")

    async def add_audit_event(self, event: ApprovalAuditEvent) -> None:
        self._session.add(
            ApprovalAuditRecord(
                id=uuid4(),
                approval_id=event.approval_id,
                transition=event.transition,
                actor_id=event.actor_id,
                occurred_at=event.occurred_at,
                correlation_id=event.correlation_id,
            )
        )
