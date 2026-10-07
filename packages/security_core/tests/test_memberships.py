from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import Base, create_session_factory
from accelerator.security_core.infrastructure.memberships import (
    ScopeMembership,
    SqlAlchemyScopeMembershipRepository,
)

CALLER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
async def repository() -> AsyncIterator[SqlAlchemyScopeMembershipRepository]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        async with factory.begin() as session:
            session.add_all(
                [
                    ScopeMembership(object_id=CALLER, scope_id="scope-a"),
                    ScopeMembership(object_id=CALLER, scope_id="scope-b"),
                    ScopeMembership(object_id=OTHER, scope_id="scope-private"),
                ]
            )
        yield SqlAlchemyScopeMembershipRepository(factory)
    finally:
        await engine.dispose()


async def test_resolves_only_memberships_for_validated_object_id(
    repository: SqlAlchemyScopeMembershipRepository,
) -> None:
    assert await repository.scope_ids_for(CALLER) == frozenset({"scope-a", "scope-b"})
    assert await repository.scope_ids_for(OTHER) == frozenset({"scope-private"})


async def test_unknown_user_has_no_scopes(
    repository: SqlAlchemyScopeMembershipRepository,
) -> None:
    assert await repository.scope_ids_for("unknown") == frozenset()


async def test_object_id_cannot_inject_query(
    repository: SqlAlchemyScopeMembershipRepository,
) -> None:
    assert await repository.scope_ids_for("' OR 1=1 --") == frozenset()


async def test_duplicate_membership_is_rejected() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        with pytest.raises(IntegrityError):
            async with factory.begin() as session:
                session.add_all(
                    [
                        ScopeMembership(object_id=CALLER, scope_id="scope-a"),
                        ScopeMembership(object_id=CALLER, scope_id="scope-a"),
                    ]
                )
    finally:
        await engine.dispose()


async def test_database_failure_is_not_disguised_as_empty_membership() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        repository = SqlAlchemyScopeMembershipRepository(create_session_factory(engine))
        with pytest.raises(OperationalError):
            await repository.scope_ids_for(CALLER)
    finally:
        await engine.dispose()


def test_context_is_immutable_and_preserves_correlation() -> None:
    context = ExecutionContext(
        correlation_id="correlation-123",
        user_id=CALLER,
        roles=frozenset({"Reader"}),
        scope_ids=frozenset({"scope-a"}),
        deadline_utc=datetime.now(UTC) + timedelta(seconds=30),
    )
    assert context.correlation_id == "correlation-123"
    with pytest.raises(ValidationError, match="frozen"):
        context.scope_ids = frozenset({"scope-private"})


def test_context_rejects_naive_deadline() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ExecutionContext(
            correlation_id="correlation-123",
            user_id=CALLER,
            roles=frozenset(),
            scope_ids=frozenset(),
            deadline_utc=datetime(2026, 1, 1),
        )
