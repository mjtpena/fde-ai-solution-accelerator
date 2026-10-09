"""PostgreSQL audit persistence against a freshly migrated database (TEST_POSTGRES_DSN)."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.api.app import create_app
from accelerator.api.audit import configure_audit, get_audit_recorder
from accelerator.application.audit import AuditRecorder
from accelerator.configuration.settings import Settings
from accelerator.domain.audit import AuditEvent, AuditPage, EventOutcome, EventType
from accelerator.infrastructure.audit import PostgresAuditRepository
from accelerator.identity.authentication import AppRole, Principal, get_current_principal
from accelerator.identity.scope_resolver import configure_scope_resolver, get_execution_context
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import create_session_factory
from accelerator.security_core.infrastructure.memberships import (
    ScopeMembership,
    SqlAlchemyScopeMembershipRepository,
)


async def test_postgres_append_query_and_database_immutability(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = create_session_factory(engine)
    repository = PostgresAuditRepository(sessions)
    try:
        correlation_id = str(uuid4())
        await AuditRecorder(repository).auth_failure(correlation_id)
        page = await repository.query(limit=1, offset=0, event_type=EventType.AUTH_FAILURE)
        assert len(page.items) == 1
        event = page.items[0]
        assert event.correlation_id == correlation_id
        assert event.actor_id is None
        assert event.occurred_at.tzinfo is not None

        with pytest.raises(IntegrityError):
            await repository.append(event)
        for statement in (
            "UPDATE audit_event SET correlation_id = 'modified'",
            "DELETE FROM audit_event",
            "TRUNCATE audit_event",
        ):
            async with sessions() as session:
                with pytest.raises(DBAPIError, match="append-only"):
                    await session.execute(text(statement))
                await session.rollback()
        assert (await repository.query(limit=1, offset=0)).items == (event,)
        assert (await repository.query(limit=1, offset=1)).items == ()
        assert (await repository.query(limit=1, offset=0, event_type=EventType.APPROVAL)).items == ()
        with pytest.raises(ValueError):
            await repository.query(limit=101, offset=0)
        with pytest.raises(ValueError):
            await repository.query(limit=1, offset=10_001)

        for event_type, outcome, fields in (
            (EventType.APPROVAL, EventOutcome.APPROVED, {"approval_id": uuid4()}),
            (EventType.TOOL_EXECUTION, EventOutcome.SUCCEEDED, {"tool_name": "read_document"}),
        ):
            persisted = AuditEvent.model_validate(
                {
                    "event_type": event_type,
                    "outcome": outcome,
                    "correlation_id": correlation_id,
                    "actor_id": "user-1",
                    **fields,
                }
            )
            await repository.append(persisted)
            assert (await repository.query(limit=1, offset=0, event_type=event_type)).items == (
                persisted,
            )

        for invalid_outcome in ("approved", "succeeded"):
            async with sessions() as session:
                with pytest.raises(IntegrityError):
                    await session.execute(
                        text(
                            "INSERT INTO audit_event "
                            "(event_id, occurred_at, event_type, outcome, correlation_id, actor_id) "
                            "VALUES (:id, now(), 'tool_execution', :outcome, 'invalid', 'actor')"
                        ),
                        {"id": uuid4(), "outcome": invalid_outcome},
                    )
                await session.rollback()
        assert len((await repository.query(limit=100, offset=0)).items) == 3

        # Deliberately tie timestamps: the UUID ordering must break the tie consistently.
        timestamp = datetime(2100, 1, 1, tzinfo=timezone.utc)
        tied_events = sorted(
            (
                AuditEvent(
                    event_type=EventType.AUTH_FAILURE,
                    outcome=EventOutcome.FAILED,
                    correlation_id=correlation_id,
                    occurred_at=timestamp,
                )
                for _ in range(2)
            ),
            key=lambda e: e.event_id,
            reverse=True,
        )
        for tied in reversed(tied_events):
            await repository.append(tied)
        assert (await repository.query(limit=1, offset=0)).items == (tied_events[0],)
        assert (await repository.query(limit=1, offset=1)).items == (tied_events[1],)
    finally:
        await engine.dispose()


async def test_http_audit_uses_shared_postgres_and_trusted_execution_context(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = create_session_factory(engine)
    actor_id = str(uuid4())
    app = create_app(
        Settings(
            environment="test", entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            entra_audience="api://test",
        )
    )
    configure_audit(app, factory)
    configure_scope_resolver(app, SqlAlchemyScopeMembershipRepository(factory))

    async def admin() -> Principal:
        return Principal(
            subject="not-object-id", object_id=actor_id, roles=frozenset({AppRole.ADMIN})
        )

    async def reader() -> Principal:
        return Principal(
            subject="not-object-id", object_id=actor_id, roles=frozenset({AppRole.READER})
        )

    # Test-only routes exercise future API hook consumers; no product write endpoints are added.
    @app.post("/test-hooks")
    async def hooks(
        recorder: AuditRecorder = Depends(get_audit_recorder),
        context: ExecutionContext = Depends(get_execution_context),
    ) -> None:
        assert context.user_id == actor_id
        assert context.scope_ids == frozenset({"allowed"})
        await recorder.approval(context, approval_id=uuid4(), outcome=EventOutcome.APPROVED)
        await recorder.tool_execution(context, tool_name="read_document", outcome=EventOutcome.FAILED)

    try:
        async with factory.begin() as session:
            session.add(ScopeMembership(object_id=actor_id, scope_id="allowed"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.get("/audit-events?token=secret-query")
            assert denied.status_code == 401
            app.dependency_overrides[get_current_principal] = reader
            assert (await client.get("/audit-events?roles=Admin")).status_code == 403
            app.dependency_overrides[get_current_principal] = admin
            recorded = await client.post("/test-hooks?user_id=forged&scope_ids=forged")
            assert recorded.status_code == 200
            page = await client.get("/audit-events?limit=100")
            assert page.status_code == 200
            events = AuditPage.model_validate(page.json()).items
            auth_event = next(
                event for event in events
                if event.correlation_id == denied.headers["x-correlation-id"]
            )
            assert auth_event.event_type == EventType.AUTH_FAILURE
            assert auth_event.actor_id is None
            hook_events = [
                event for event in events
                if event.correlation_id == recorded.headers["x-correlation-id"]
            ]
            assert {event.event_type for event in hook_events} == {
                EventType.APPROVAL, EventType.TOOL_EXECUTION
            }
            assert all(event.actor_id == actor_id for event in hook_events)
            assert "secret-query" not in page.text
    finally:
        await engine.dispose()
