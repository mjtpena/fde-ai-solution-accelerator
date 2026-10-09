from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    insert,
    literal,
    null,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from accelerator.agent_core.approvals import Approval, ApprovalAuditEvent, ApprovalStatus
from accelerator.infrastructure.audit import audit_event

# Decisions and executions also go to the central append-only audit log, inside the
# approval's own transaction, so the two logs can never disagree.
_CENTRAL_AUDIT = {
    "approved": ("approval", "approved"),
    "rejected": ("approval", "denied"),
    "executed": ("tool_execution", "succeeded"),
}


class ApprovalBase(DeclarativeBase):
    pass


class ApprovalRecord(ApprovalBase):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executed', 'expired')",
            name="approvals_valid_status",
        ),
        Index("ix_approvals_scope_status", "scope_id", "status"),
    )

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
            status=cast(ApprovalStatus, self.status),  # validated by the Pydantic model
            decided_by=self.decided_by,
            expires_at=self.expires_at,
            correlation_id=self.correlation_id,
        )


class ApprovalAuditRecord(ApprovalBase):
    __tablename__ = "approval_audit_events"
    __table_args__ = (Index("ix_approval_audit_events_approval", "approval_id"),)

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
        # ``ApprovalAuditRecord`` has no declared ORM relationship to
        # ``ApprovalRecord`` (they are independent tables with a raw FK
        # column), so the unit-of-work cannot infer that this insert must
        # precede a same-flush audit-event insert for the same approval.
        # Flushing immediately pins the insert order and prevents a foreign
        # key violation against a database that enforces it (observed with
        # real PostgreSQL; masked by SQLite's default FK enforcement).
        await self._session.flush()

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

    async def list_pending(
        self, scope_ids: frozenset[str], *, now: datetime, limit: int
    ) -> list[Approval]:
        """Unexpired pending approvals in the caller's server-resolved scopes."""
        if not scope_ids:
            return []
        statement = (
            select(ApprovalRecord)
            .where(
                ApprovalRecord.scope_id.in_(sorted(scope_ids)),
                ApprovalRecord.status == "pending",
                ApprovalRecord.expires_at > now,
            )
            .order_by(ApprovalRecord.expires_at, ApprovalRecord.id)
            .limit(limit)
        )
        records = (await self._session.scalars(statement)).all()
        return [record.to_approval() for record in records]

    async def add_audit_event(self, event: ApprovalAuditEvent) -> None:
        central = _CENTRAL_AUDIT.get(event.transition)
        if central is not None:
            event_type, outcome = central
            await self._session.execute(
                insert(audit_event).from_select(
                    [
                        "event_id",
                        "occurred_at",
                        "event_type",
                        "outcome",
                        "correlation_id",
                        "actor_id",
                        "approval_id",
                        "tool_name",
                    ],
                    select(
                        literal(uuid4(), Uuid),
                        literal(event.occurred_at, DateTime(timezone=True)),
                        literal(event_type),
                        literal(outcome),
                        literal(event.correlation_id),
                        literal(event.actor_id),
                        literal(event.approval_id, Uuid) if event_type == "approval" else null(),
                        ApprovalRecord.tool_name if event_type == "tool_execution" else null(),
                    ).where(ApprovalRecord.id == event.approval_id),
                )
            )
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
