"""Explicitly mounted offline provider for container smoke; never included in the image."""

from dataclasses import dataclass

from accelerator.agent_core.hosting.contracts import (
    HostedAbstention,
    InvocationResult,
    InvocationUnauthorized,
)


OFFLINE_AUTHORIZATION = 'Bearer offline-container-test'


class OfflineApplication:
    async def invoke(self, query: str, authorization: str | None) -> InvocationResult:
        if authorization != OFFLINE_AUTHORIZATION:
            raise InvocationUnauthorized()
        return InvocationResult(
            status="abstained",
            answer=None,
            citations=(),
            abstention=HostedAbstention(reason="Offline fixture has no evidence", evidence_ids=()),
        )


@dataclass(frozen=True)
class OfflineContext:
    scope_id: str = "offline-test"


class OfflineResolver:
    async def resolve(self, authorization: str | None) -> OfflineContext:
        if authorization != OFFLINE_AUTHORIZATION:
            raise InvocationUnauthorized()
        return OfflineContext()


class OfflineWorkflow:
    async def run(self, query: str, ctx: OfflineContext) -> InvocationResult:
        return InvocationResult(
            status="abstained",
            answer=None,
            citations=(),
            abstention=HostedAbstention(reason="Offline fixture has no evidence", evidence_ids=()),
        )


def create_application() -> OfflineApplication:
    return OfflineApplication()


def create_resolver() -> OfflineResolver:
    return OfflineResolver()


def create_workflow() -> OfflineWorkflow:
    return OfflineWorkflow()
