"""Explicitly mounted offline provider for container smoke; never included in the image."""

from accelerator.agent_core.hosting.contracts import (
    HostedAbstention,
    InvocationResult,
    InvocationUnauthorized,
)


class OfflineApplication:
    async def invoke(self, query: str, authorization: str | None) -> InvocationResult:
        if authorization != "Bearer offline-container-test":
            raise InvocationUnauthorized()
        return InvocationResult(
            status="abstained",
            answer=None,
            citations=(),
            abstention=HostedAbstention(reason="Offline fixture has no evidence", evidence_ids=()),
        )


def create_application() -> OfflineApplication:
    return OfflineApplication()
