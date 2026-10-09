"""Queue consumption with retry, exponential backoff and a poison path.

A message is deleted only after it was handled, or after it was moved to the
poison queue with its document recorded as ``failed(reason)``. Transient failures
make the message invisible for an exponentially growing delay and leave it to be
redelivered; permanent ones (invalid message, rejected document) and messages
that exhausted their attempts go straight to the poison path.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from .pipeline import IngestionMessage, RejectedDocument

logger = logging.getLogger("ingestion_worker")


class QueueMessage(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def content(self) -> Any: ...

    @property
    def dequeue_count(self) -> int | None: ...

    @property
    def pop_receipt(self) -> str | None: ...


class Queue(Protocol):
    """The subset of ``azure.storage.queue.aio.QueueClient`` the worker uses."""

    def receive_message(self, *, visibility_timeout: int | None = None) -> Awaitable[Any]: ...

    def delete_message(self, message: Any, pop_receipt: str | None = None) -> Awaitable[None]: ...

    def update_message(
        self, message: Any, pop_receipt: str | None = None, *, visibility_timeout: int | None = None
    ) -> Awaitable[Any]: ...

    def send_message(self, content: Any) -> Awaitable[Any]: ...


Handler = Callable[[IngestionMessage], Awaitable[None]]
FailureRecorder = Callable[[IngestionMessage, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: int = 15
    max_delay_seconds: int = 900
    visibility_timeout_seconds: int = 300

    def delay_for(self, attempt: int) -> int:
        delay: int = self.base_delay_seconds * 2 ** max(0, attempt - 1)
        return min(self.max_delay_seconds, delay)


DEFAULT_RETRY = RetryPolicy()


class QueueConsumer:
    def __init__(
        self,
        queue: Queue,
        poison_queue: Queue,
        handle: Handler,
        record_failure: FailureRecorder,
        *,
        retry: RetryPolicy = DEFAULT_RETRY,
        idle_poll_seconds: float = 1.0,
        max_idle_poll_seconds: float = 30.0,
    ) -> None:
        self._queue = queue
        self._poison = poison_queue
        self._handle = handle
        self._record_failure = record_failure
        self._retry = retry
        self._idle_poll_seconds = idle_poll_seconds
        self._max_idle_poll_seconds = max_idle_poll_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        idle_delay = self._idle_poll_seconds
        while not stop_event.is_set():
            processed = await self.drain_once(stop_event)
            if processed:
                idle_delay = self._idle_poll_seconds
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=idle_delay)
            except TimeoutError:
                idle_delay = min(idle_delay * 2, self._max_idle_poll_seconds)

    async def drain_once(self, stop_event: asyncio.Event | None = None) -> int:
        """Process visible messages one at a time until the queue is empty or a stop.

        Each message is received just before it is handled, so its visibility timeout
        covers only its own processing and never waits behind other messages.
        """
        processed = 0
        while stop_event is None or not stop_event.is_set():
            message = await self._queue.receive_message(
                visibility_timeout=self._retry.visibility_timeout_seconds
            )
            if message is None:
                break
            await self.process(message)
            processed += 1
        return processed

    async def process(self, message: QueueMessage) -> None:
        attempt = message.dequeue_count or 1
        try:
            request = IngestionMessage.model_validate_json(_text(message.content))
        except ValidationError as error:
            logger.error(
                "ingestion_message_invalid",
                extra={"message_id": message.id, "error_count": error.error_count()},
            )
            await self._poison_message(message, None, "invalid message")
            return
        context = {"message_id": message.id, "document_id": request.document_id, "attempt": attempt}
        try:
            await self._handle(request)
        except RejectedDocument as error:
            logger.warning("ingestion_rejected", extra={**context, "reason": str(error)})
            await self._poison_message(message, request, f"rejected: {error}")
            return
        except Exception as error:  # every other failure is retried, then poisoned
            reason = f"{type(error).__name__} after {attempt} attempt(s)"
            if attempt >= self._retry.max_attempts:
                logger.error("ingestion_poisoned", extra={**context, "reason": reason})
                await self._poison_message(message, request, reason)
                return
            delay = self._retry.delay_for(attempt)
            logger.warning(
                "ingestion_retry_scheduled",
                extra={**context, "reason": reason, "delay_seconds": delay},
            )
            await self._queue.update_message(
                message, pop_receipt=message.pop_receipt, visibility_timeout=delay
            )
            return
        await self._queue.delete_message(message, pop_receipt=message.pop_receipt)
        logger.info("ingestion_message_completed", extra=context)

    async def _poison_message(
        self, message: QueueMessage, request: IngestionMessage | None, reason: str
    ) -> None:
        if request is not None and request.operation == "ingest":
            await self._record_failure(request, reason[:500])
        await self._poison.send_message(
            json.dumps(
                {
                    "message_id": message.id,
                    "reason": reason[:500],
                    "content": _text(message.content),
                }
            )
        )
        await self._queue.delete_message(message, pop_receipt=message.pop_receipt)


def _text(content: Any) -> str:
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content)
