from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Table,
    Uuid,
    insert,
    select,
)

from accelerator.domain.audit import AuditEvent, AuditPage, EventType
from accelerator.security_core.infrastructure.database import Base, SessionFactory

audit_event = Table(
    "audit_event",
    Base.metadata,
    Column("event_id", Uuid, primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("event_type", String(32), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("correlation_id", String(128), nullable=False),
    Column("actor_id", String(128)),
    Column("approval_id", Uuid),
    Column("tool_name", String(128)),
    Column("reason_code", String(64)),
    CheckConstraint(
        "(event_type = 'auth_failure' AND outcome = 'failed' "
        "AND actor_id IS NULL AND approval_id IS NULL AND tool_name IS NULL) OR "
        "(event_type = 'authorization_failure' AND outcome = 'denied' "
        "AND approval_id IS NULL AND tool_name IS NULL) OR "
        "(event_type = 'approval' AND outcome IN ('approved', 'denied') "
        "AND actor_id IS NOT NULL AND approval_id IS NOT NULL AND tool_name IS NULL) OR "
        "(event_type = 'tool_execution' AND outcome IN ('succeeded', 'failed') "
        "AND actor_id IS NOT NULL AND approval_id IS NULL AND tool_name IS NOT NULL) OR "
        "(event_type = 'content_safety' AND actor_id IS NOT NULL "
        "AND approval_id IS NULL AND tool_name IS NULL AND reason_code IS NOT NULL)",
        name="audit_event_valid_fields",
    ),
)
Index("ix_audit_event_order", audit_event.c.occurred_at.desc(), audit_event.c.event_id.desc())
Index(
    "ix_audit_event_type_order",
    audit_event.c.event_type,
    audit_event.c.occurred_at.desc(),
    audit_event.c.event_id.desc(),
)


class PostgresAuditRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def append(self, event: AuditEvent) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(insert(audit_event).values(**event.model_dump()))

    async def query(
        self, *, limit: int, offset: int, event_type: EventType | None = None
    ) -> AuditPage:
        if not 1 <= limit <= 100 or not 0 <= offset <= 10_000:
            raise ValueError("Audit pagination exceeds allowed bounds")
        statement = select(audit_event)
        if event_type is not None:
            statement = statement.where(audit_event.c.event_type == event_type.value)
        statement = statement.order_by(
            audit_event.c.occurred_at.desc(), audit_event.c.event_id.desc()
        ).limit(limit).offset(offset)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).mappings()
            return AuditPage(items=tuple(AuditEvent.model_validate(row) for row in rows))
