"""Audit content-safety refusals of chat turns.

Every turn the grounded-answer workflow refuses on content-safety grounds (a prompt
attack, a blocked answer, or a screening outage that forced a refusal) writes one
``content_safety`` audit event with the actor, the correlation ID and the stable
reason code. Prompts, evidence and answers are never recorded. A failed audit
write propagates: the turn fails rather than refusing silently.
"""

import logging
from typing import cast, get_args

from pydantic import BaseModel

from accelerator.agent_core.workflows.grounded_answer import (
    CONTENT_SAFETY_CODES,
    GroundedAnswerResult,
)
from accelerator.api.chat import ChatTurnPort
from accelerator.application.audit import AuditRecorder
from accelerator.domain.audit import ContentSafetyReason
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy import ApprovalRequired

logger = logging.getLogger(__name__)

if frozenset(get_args(ContentSafetyReason)) != CONTENT_SAFETY_CODES:  # pragma: no cover
    raise RuntimeError("Audit reason codes must match the workflow's content-safety codes.")


class AuditedRefusalsChatTurn:
    def __init__(self, inner: ChatTurnPort, recorder: AuditRecorder | None) -> None:
        self._inner = inner
        self._recorder = recorder

    async def run(
        self, query: str, ctx: ExecutionContext
    ) -> GroundedAnswerResult | ApprovalRequired[BaseModel]:
        result = await self._inner.run(query, ctx)
        if isinstance(result, GroundedAnswerResult) and result.screened_out_chunk_ids:
            logger.warning(
                "content_safety_chunks_dropped",
                extra={
                    "correlation_id": ctx.correlation_id,
                    "chunk_ids": list(result.screened_out_chunk_ids),
                },
            )
        if (
            not isinstance(result, GroundedAnswerResult)
            or result.abstention is None
            or result.abstention.code not in CONTENT_SAFETY_CODES
        ):
            return result
        code = cast(ContentSafetyReason, result.abstention.code)
        logger.warning(
            "content_safety_refusal",
            extra={"correlation_id": ctx.correlation_id, "reason_code": code},
        )
        if self._recorder is None:
            logger.error(
                "content_safety_refusal_unaudited",
                extra={"correlation_id": ctx.correlation_id, "reason_code": code},
            )
        else:
            await self._recorder.content_safety_refusal(ctx, reason_code=code)
        return result
