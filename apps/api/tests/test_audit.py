from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from accelerator.api.app import create_app
from accelerator.application.audit import AuditRecorder
from accelerator.configuration.settings import Settings
from accelerator.domain.audit import (
    AuditEvent,
    AuditPage,
    EventOutcome,
    EventType,
)
from accelerator.identity.authentication import AppRole, Principal, get_current_principal
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.security_core.data_boundaries.context import ExecutionContext


class Context(ExecutionContext):
    correlation_id: str = "correlation-1"
    user_id: str = "user-1"
    roles: frozenset[str] = frozenset({"Admin"})
    scope_ids: frozenset[str] = frozenset()
    deadline_utc: datetime = datetime(2100, 1, 1, tzinfo=UTC)


def make_app(repository: "MemoryRepository | None" = None) -> FastAPI:
    return create_app(
        Settings(
            environment="test",
            entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            entra_audience="api://test",
        ),
        audit_repository=repository,
    )


async def principal() -> Principal:
    return Principal(subject="user-1", object_id="user-1", roles=frozenset({AppRole.ADMIN}))


class MemoryRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def query(
        self, *, limit: int, offset: int, event_type: EventType | None = None
    ) -> AuditPage:
        events = [e for e in self.events if event_type is None or e.event_type == event_type]
        events.sort(key=lambda e: (e.occurred_at, e.event_id), reverse=True)
        return AuditPage(items=tuple(events[offset:offset + limit]))


def app_with_context(repository: MemoryRepository, context: Context) -> FastAPI:
    app = make_app(repository)

    async def authenticated_context() -> Context:
        return context

    app.dependency_overrides[get_execution_context] = authenticated_context
    app.dependency_overrides[get_current_principal] = principal
    return app


@pytest.mark.parametrize("outcome", [EventOutcome.APPROVED, EventOutcome.DENIED])
async def test_approval_hook_records_only_safe_fields(
    outcome: Literal[EventOutcome.APPROVED, EventOutcome.DENIED],
) -> None:
    repository = MemoryRepository()
    approval_id = uuid4()
    await AuditRecorder(repository).approval(Context(), approval_id=approval_id, outcome=outcome)
    event = repository.events[0]
    assert event.event_type == EventType.APPROVAL
    assert event.outcome == outcome
    assert event.approval_id == approval_id
    assert event.actor_id == "user-1"
    assert event.correlation_id == "correlation-1"
    assert event.tool_name is None


@pytest.mark.parametrize("outcome", [EventOutcome.SUCCEEDED, EventOutcome.FAILED])
async def test_tool_hook_records_success_and_failure(
    outcome: Literal[EventOutcome.SUCCEEDED, EventOutcome.FAILED],
) -> None:
    repository = MemoryRepository()
    await AuditRecorder(repository).tool_execution(
        Context(), tool_name="read_document", outcome=outcome
    )
    event = repository.events[0]
    assert event.event_type == EventType.TOOL_EXECUTION
    assert event.outcome == outcome
    assert event.actor_id == "user-1"
    assert event.tool_name == "read_document"


def test_event_is_frozen_and_disallows_arbitrary_payloads() -> None:
    event = AuditEvent(
        event_type=EventType.AUTH_FAILURE, outcome=EventOutcome.FAILED, correlation_id="c"
    )
    with pytest.raises(ValidationError, match="frozen"):
        event.actor_id = "modified"
    with pytest.raises(ValidationError, match="Extra inputs"):
        AuditEvent.model_validate({**event.model_dump(), "token": "sensitive"})
    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**event.model_dump(), "occurred_at": "2026-01-01T00:00:00"})


@pytest.mark.parametrize(
    ("event_type", "outcome"),
    [
        (EventType.AUTH_FAILURE, EventOutcome.SUCCEEDED),
        (EventType.APPROVAL, EventOutcome.FAILED),
        (EventType.TOOL_EXECUTION, EventOutcome.APPROVED),
    ],
)
def test_invalid_event_type_combinations_are_rejected(
    event_type: EventType, outcome: EventOutcome
) -> None:
    with pytest.raises(ValidationError):
        AuditEvent(event_type=event_type, outcome=outcome, correlation_id="c")


async def test_tool_hook_rejects_payload_disguised_as_tool_name() -> None:
    repository = MemoryRepository()
    with pytest.raises(ValidationError):
        await AuditRecorder(repository).tool_execution(
            Context(), tool_name="Bearer sensitive credential", outcome=EventOutcome.FAILED
        )
    assert repository.events == []


@pytest.mark.parametrize("invalid_token", [False, True])
async def test_auth_failure_is_recorded_without_request_secrets(invalid_token: bool) -> None:
    repository = MemoryRepository()
    app = make_app(repository)

    class RejectingValidator:
        async def validate(
            self, token: str, client: httpx.AsyncClient, *, correlation_id: str | None = None
        ) -> Principal:
            raise ValueError("secret-validator-detail")

    app.state.token_validator = RejectingValidator()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        app.state.http_client = c
        headers = {"X-Correlation-ID": str(uuid4())}
        if invalid_token:
            headers["Authorization"] = "Bearer secret-jwt"
        response = await c.get(
            "/audit-events?token=secret-query",
            headers=headers,
        )
    assert response.status_code == 401
    assert len(repository.events) == 1
    event = repository.events[0]
    assert event.event_type == EventType.AUTH_FAILURE
    assert event.outcome == EventOutcome.FAILED
    assert event.actor_id is None
    UUID(event.correlation_id)
    assert event.correlation_id == response.headers["x-correlation-id"]
    assert "secret" not in event.model_dump_json()


async def test_non_admin_cannot_read_events_or_forge_roles_in_headers() -> None:
    repository = MemoryRepository()
    app = app_with_context(repository, Context(roles=frozenset({"Reader"})))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.get("/audit-events?roles=Admin", headers={"X-Roles": "Admin"})
    assert response.status_code == 403
    # The denial itself is audited (authorization_failure), nothing else is written.
    assert [event.event_type for event in repository.events] == [
        EventType.AUTHORIZATION_FAILURE
    ]


async def test_admin_query_is_bounded_ordered_and_filterable() -> None:
    repository = MemoryRepository()
    recorder = AuditRecorder(repository)
    await recorder.auth_failure("first")
    await recorder.approval(Context(), approval_id=uuid4(), outcome=EventOutcome.APPROVED)
    await recorder.auth_failure("last")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    repository.events = [
        AuditEvent.model_validate(
            {**event.model_dump(), "occurred_at": start + timedelta(seconds=index)}
        )
        for index, event in enumerate(repository.events)
    ]
    app = app_with_context(repository, Context())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        first = await c.get("/audit-events?limit=1")
        second = await c.get("/audit-events?limit=1&offset=1")
        filtered = await c.get("/audit-events?event_type=auth_failure")
        empty = await c.get("/audit-events?offset=10000")
    assert first.status_code == second.status_code == filtered.status_code == 200
    assert first.json()["items"][0]["correlation_id"] == "last"
    assert second.json()["items"][0]["event_type"] == "approval"
    assert len(filtered.json()["items"]) == 2
    assert empty.json() == {"items": []}
    assert len(repository.events) == 3


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "offset=-1", "offset=10001", "event_type=unknown"]
)
async def test_invalid_pagination_or_event_type_is_rejected(query: str) -> None:
    app = app_with_context(MemoryRepository(), Context())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.get(f"/audit-events?{query}")
    assert response.status_code == 422


async def test_successful_health_requests_do_not_generate_audit_events() -> None:
    repository = MemoryRepository()
    app = make_app(repository)
    app.dependency_overrides[get_current_principal] = principal
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/healthz")).status_code == 200
    assert repository.events == []


async def test_authentication_dependency_failures_are_audited() -> None:
    repository = MemoryRepository()
    app = make_app(repository)
    app.dependency_overrides[get_current_principal] = principal

    async def invalid_token() -> Context:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    app.dependency_overrides[get_execution_context] = invalid_token
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/audit-events")).status_code == 401
    assert len(repository.events) == 1


async def test_persistence_failure_propagates_instead_of_returning_success() -> None:
    class FailingRepository(MemoryRepository):
        async def append(self, event: AuditEvent) -> None:
            raise RuntimeError("Persistence unavailable")

    repository = FailingRepository()
    app = make_app(repository)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        with pytest.raises(RuntimeError, match="Persistence unavailable"):
            await c.get("/audit-events")


async def test_missing_persistence_is_an_explicit_configuration_error() -> None:
    app = make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.get("/audit-events")
    assert response.status_code == 503
    assert response.json() == {"detail": "Audit persistence is unavailable."}


async def test_database_failure_replaces_401_with_correlated_503(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingRepository(MemoryRepository):
        async def append(self, event: AuditEvent) -> None:
            raise SQLAlchemyError("secret-driver-detail")

    app = make_app(FailingRepository())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.get("/audit-events")
    assert response.status_code == 503
    assert response.json() == {"detail": "Audit persistence is unavailable."}
    assert any(
        record.message == "auth_failure_audit_failed"
        and getattr(record, "correlation_id", None) == response.headers["x-correlation-id"]
        for record in caplog.records
    )
    assert "secret-driver-detail" not in caplog.text


async def test_query_database_failure_returns_correlated_503(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingRepository(MemoryRepository):
        async def query(
            self, *, limit: int, offset: int, event_type: EventType | None = None
        ) -> AuditPage:
            raise SQLAlchemyError("secret-driver-detail")

    app = app_with_context(FailingRepository(), Context())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.get("/audit-events")
    assert response.status_code == 503
    assert any(
        record.message == "audit_query_failed"
        and getattr(record, "correlation_id", None) == "correlation-1"
        for record in caplog.records
    )
    assert "secret-driver-detail" not in caplog.text


def test_public_surface_has_no_mutation_routes_or_repository_methods() -> None:
    app = make_app(MemoryRepository())
    assert set(app.openapi()["paths"]["/audit-events"]) == {"get"}
    from accelerator.infrastructure.audit import PostgresAuditRepository

    assert not hasattr(PostgresAuditRepository, "update")
    assert not hasattr(PostgresAuditRepository, "delete")


def test_openapi_declares_audit_outages_for_all_authenticated_routes() -> None:
    schema = make_app(MemoryRepository()).openapi()
    assert "503" in schema["paths"]["/audit-events"]["get"]["responses"]
    assert "401" in schema["paths"]["/audit-events"]["get"]["responses"]
    assert "401" not in schema["paths"]["/healthz"]["get"]["responses"]


def test_response_schema_requires_always_serialized_event_fields() -> None:
    schema = make_app(MemoryRepository()).openapi()
    response_ref = schema["paths"]["/audit-events"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    page_schema = schema["components"]["schemas"][response_ref.rsplit("/", 1)[-1]]
    event_ref = page_schema["properties"]["items"]["items"]["$ref"]
    event_schema = schema["components"]["schemas"][event_ref.rsplit("/", 1)[-1]]
    event = AuditEvent(
        event_type=EventType.AUTH_FAILURE, outcome=EventOutcome.FAILED, correlation_id="c"
    )
    assert set(event.model_dump()) == set(event_schema["required"])
    assert {"event_id", "occurred_at"} <= set(event_schema["required"])
    assert "event_id" not in AuditEvent.model_json_schema(mode="validation")["required"]


def throttled_app(repository: MemoryRepository, **limits: int) -> FastAPI:
    return create_app(
        Settings(
            environment="test",
            entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            entra_audience="api://test",
            **limits,
        ),
        audit_repository=repository,
    )


async def test_unauthenticated_audit_inserts_are_throttled_per_client() -> None:
    repository = MemoryRepository()
    app = throttled_app(repository, auth_failure_audit_per_client_limit=2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 1234)),
        base_url="http://test",
    ) as c:
        statuses = [(await c.get("/audit-events")).status_code for _ in range(5)]

    # Every caller still gets a 401; only the first two failures cost a database write.
    assert statuses == [401] * 5
    assert len(repository.events) == 2


async def test_throttle_is_per_client_and_globally_capped() -> None:
    repository = MemoryRepository()
    app = throttled_app(
        repository, auth_failure_audit_per_client_limit=1, auth_failure_audit_global_limit=2
    )
    for host in ("198.51.100.1", "198.51.100.2", "198.51.100.3"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(host, 1)), base_url="http://test"
        ) as c:
            assert (await c.get("/audit-events")).status_code == 401

    assert len(repository.events) == 2


def test_suppressed_failures_are_counted_for_the_next_audit_log_line() -> None:
    from accelerator.api.audit import AuthFailureAuditThrottle

    throttle = AuthFailureAuditThrottle(per_client_limit=1, global_limit=10, window_seconds=60)

    assert throttle.allow("client-a")
    assert not throttle.allow("client-a")
    assert not throttle.allow("client-a")
    assert throttle.allow("client-b")
    assert throttle.take_suppressed_count() == 2
    assert throttle.take_suppressed_count() == 0


def test_globally_throttled_floods_allocate_no_per_client_state() -> None:
    from accelerator.api.audit import AuthFailureAuditThrottle

    throttle = AuthFailureAuditThrottle(per_client_limit=5, global_limit=2, window_seconds=60)
    allowed = [throttle.allow(f"198.51.100.{index}") for index in range(200)]

    assert allowed.count(True) == 2
    assert len(throttle._per_client._requests) == 2


async def test_forbidden_responses_are_audited_with_the_validated_actor() -> None:
    repository = MemoryRepository()
    app = make_app(repository)

    async def reader_context() -> Context:
        return Context(roles=frozenset({"Reader"}))

    async def reader() -> Principal:
        return Principal(subject="s", object_id="reader-1", roles=frozenset({AppRole.READER}))

    app.dependency_overrides[get_execution_context] = reader_context
    app.dependency_overrides[get_current_principal] = reader
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.get("/audit-events")

    assert response.status_code == 403
    [event] = repository.events
    assert event.event_type == EventType.AUTHORIZATION_FAILURE
    assert event.outcome == EventOutcome.DENIED
    assert event.correlation_id == response.headers["x-correlation-id"]


def test_authorization_failure_events_reject_approval_or_tool_fields() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(
            event_type=EventType.AUTHORIZATION_FAILURE,
            outcome=EventOutcome.DENIED,
            correlation_id="c",
            tool_name="read_document",
        )
    with pytest.raises(ValidationError):
        AuditEvent(
            event_type=EventType.AUTHORIZATION_FAILURE,
            outcome=EventOutcome.FAILED,
            correlation_id="c",
        )
