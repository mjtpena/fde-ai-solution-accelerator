"""Explicitly mounted offline provider for container smoke; never included in the image."""

from dataclasses import dataclass

from accelerator.agent_core.hosting.contracts import (
    HostedAbstention,
    InvocationResult,
    InvocationUnauthorized,
)
from accelerator.security_core.content_safety import ContentSafetyChecker, ContentSafetyPolicy

OFFLINE_AUTHORIZATION = 'Bearer offline-container-test'


class OfflineApplication:
    async def invoke(self, query: str, authorization: str | None) -> InvocationResult:
        if authorization != OFFLINE_AUTHORIZATION:
            raise InvocationUnauthorized()
        return InvocationResult(
            status="abstained",
            answer=None,
            citations=(),
            abstention=HostedAbstention(
                reason="Offline fixture has no evidence",
                evidence_ids=(),
                code="insufficient_evidence",
            ),
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
    """Screens the prompt like the grounded workflow, then abstains."""

    def __init__(self, checker: ContentSafetyChecker | None = None) -> None:
        self.checker = checker

    async def run(self, query: str, ctx: OfflineContext) -> InvocationResult:
        if self.checker is not None:
            await self.checker.shield_prompt(query, ())
        return InvocationResult(
            status="abstained",
            answer=None,
            citations=(),
            abstention=HostedAbstention(
                reason="Offline fixture has no evidence",
                evidence_ids=(),
                code="insufficient_evidence",
            ),
        )


def create_application() -> OfflineApplication:
    return OfflineApplication()


def create_resolver() -> OfflineResolver:
    return OfflineResolver()


def create_workflow(
    *,
    content_safety_checker: ContentSafetyChecker | None = None,
    content_safety_policy: ContentSafetyPolicy | None = None,
) -> OfflineWorkflow:
    return OfflineWorkflow(content_safety_checker)
