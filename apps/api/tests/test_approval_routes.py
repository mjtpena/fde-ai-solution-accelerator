"""Approval decision routes end to end on a migrated PostgreSQL database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.agent_core.approvals import ApprovalService
from accelerator.api.app import create_app
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import AppRole, Principal, get_current_principal
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.infrastructure.approvals import SQLAlchemyApprovalRepository
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import create_session_factory


class WriteArgs(BaseModel):
    record: str


def context(
    user_id: str, *roles: str, scopes: frozenset[str] = frozenset({"scope-a"})
) -> ExecutionContext:
    return ExecutionContext(
        correlation_id=str(uuid4()),
        user_id=user_id,
        roles=frozenset(roles),
        scope_ids=scopes,
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


REQUESTER = context("requester", "Contributor", "Approver")
APPROVER = context("approver", "Approver")
READER = context("reader", "Reader")
OUTSIDER = context("outsider", "Approver", scopes=frozenset({"scope-b"}))


def make_app(database_url: str) -> tuple[FastAPI, list[ExecutionContext]]:
    sessions = create_session_factory(create_async_engine(database_url))
    app = create_app(
        Settings(
            environment="test",
            entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            entra_audience="api://test",
        ),
        session_factory=sessions,
    )
    current: list[ExecutionContext] = [APPROVER]

    async def principal() -> Principal:
        return Principal(subject="s", object_id=current[0].user_id, roles=frozenset(AppRole))

    async def trusted_context() -> ExecutionContext:
        return current[0]

    app.dependency_overrides[get_current_principal] = principal
    app.dependency_overrides[get_execution_context] = trusted_context
    return app, current


@asynccontextmanager
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def request_approval(app: FastAPI, *, expires_in: timedelta = timedelta(minutes=10)) -> UUID:
    async with app.state.session_factory() as session:
        service = ApprovalService[WriteArgs, BaseModel](SQLAlchemyApprovalRepository(session))
        approval = await service.create(
            tool_name="write_record",
            args=WriteArgs(record="r-1"),
            ctx=REQUESTER,
            expires_at=datetime.now(UTC) + expires_in,
        )
    return approval.id


async def rows(database_url: str, statement: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return [tuple(row) for row in await connection.execute(text(statement))]
    finally:
        await engine.dispose()


async def test_approver_lists_and_approves_with_audit(migrated_database_url: str) -> None:
    app, current = make_app(migrated_database_url)
    approval_id = await request_approval(app)

    async with client(app) as http:
        listed = await http.get("/approvals")
        approved = await http.post(f"/approvals/{approval_id}/approve")
        replayed = await http.post(f"/approvals/{approval_id}/approve")
        current[0] = REQUESTER
        after = await http.get("/approvals")

    assert listed.status_code == 200
    [item] = listed.json()["items"]
    assert item["approval_id"] == str(approval_id)
    assert item["can_decide"] is True
    assert "args" not in item and "args_hash" not in item
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by"] == "approver"
    assert replayed.status_code == 409
    assert replayed.json()["detail"]["code"] == "approval_not_pending"
    assert after.json()["items"] == []

    transitions = await rows(
        migrated_database_url,
        f"SELECT transition, actor_id FROM approval_audit_events "
        f"WHERE approval_id = '{approval_id}' ORDER BY occurred_at",
    )
    assert transitions == [("pending", "requester"), ("approved", "approver")]
    central = await rows(
        migrated_database_url,
        f"SELECT event_type, outcome, actor_id FROM audit_event WHERE approval_id = '{approval_id}'",
    )
    assert central == [("approval", "approved", "approver")]


async def test_rejection_is_audited_as_denied(migrated_database_url: str) -> None:
    app, _ = make_app(migrated_database_url)
    approval_id = await request_approval(app)

    async with client(app) as http:
        rejected = await http.post(f"/approvals/{approval_id}/reject")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    central = await rows(
        migrated_database_url,
        f"SELECT outcome FROM audit_event WHERE approval_id = '{approval_id}'",
    )
    assert central == [("denied",)]


@pytest.mark.parametrize(
    ("caller", "status"),
    [(REQUESTER, 403), (READER, 403), (OUTSIDER, 404)],
)
async def test_requester_non_approver_and_other_scope_cannot_decide(
    migrated_database_url: str, caller: ExecutionContext, status: int
) -> None:
    app, current = make_app(migrated_database_url)
    approval_id = await request_approval(app)
    current[0] = caller

    async with client(app) as http:
        response = await http.post(f"/approvals/{approval_id}/approve")
        listed = await http.get("/approvals")

    assert response.status_code == status, response.text
    remaining = await rows(
        migrated_database_url, f"SELECT status FROM approvals WHERE id = '{approval_id}'"
    )
    assert remaining == [("pending",)]
    if caller is READER:
        assert listed.status_code == 403
    if caller is OUTSIDER:
        assert listed.json()["items"] == []
    if caller is REQUESTER:
        assert listed.json()["items"][0]["can_decide"] is False


async def test_expired_approval_cannot_be_decided(migrated_database_url: str) -> None:
    app, _ = make_app(migrated_database_url)
    approval_id = await request_approval(app, expires_in=timedelta(seconds=1))
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE approvals SET expires_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": approval_id},
        )

    async with client(app) as http:
        response = await http.post(f"/approvals/{approval_id}/approve")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "approval_expired"
    assert await rows(
        migrated_database_url, f"SELECT status FROM approvals WHERE id = '{approval_id}'"
    ) == [("expired",)]


async def test_unknown_approval_is_not_found(migrated_database_url: str) -> None:
    app, _ = make_app(migrated_database_url)

    async with client(app) as http:
        response = await http.post(f"/approvals/{uuid4()}/reject")

    assert response.status_code == 404
