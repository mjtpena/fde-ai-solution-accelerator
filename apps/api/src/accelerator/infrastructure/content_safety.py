"""Azure AI Content Safety adapter (REST over ``httpx``, Entra ID bearer tokens).

Endpoints, both generally available at ``api-version=2024-09-01``
(https://learn.microsoft.com/azure/ai-services/content-safety/quickstart-jailbreak and
https://learn.microsoft.com/azure/ai-services/content-safety/quickstart-text):

* ``POST {endpoint}/contentsafety/text:shieldPrompt`` with
  ``{"userPrompt": str, "documents": [str]}``, answering
  ``{"userPromptAnalysis": {"attackDetected": bool},
  "documentsAnalysis": [{"attackDetected": bool}]}`` in document order.
* ``POST {endpoint}/contentsafety/text:analyze`` with
  ``{"text": str, "categories": [...], "outputType": "FourSeverityLevels"}``,
  answering ``{"categoriesAnalysis": [{"category": str, "severity": int}]}``.

Input limits (Content Safety overview, "Input requirements"): a Prompt Shields user
prompt of at most 10,000 characters, at most five documents per request totalling
at most 10,000 characters, and at most 10,000 characters of ``text:analyze`` text.
Longer input is split into several requests; a document is attacked if any of its
pieces is, and a category's severity is its maximum over pieces.

Authentication is a Microsoft Entra token for ``https://cognitiveservices.azure.com/.default``
from the injected (managed identity) credential; the account disables key auth.
Every failure (transport error, timeout, non-2xx status, malformed body, elapsed
request deadline) raises ``ContentSafetyUnavailableError`` so the workflow refuses.
"""

import asyncio
from collections.abc import Coroutine, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError
from pydantic import BaseModel, ConfigDict, ValidationError

from accelerator.security_core.content_safety import (
    ContentSafetyUnavailableError,
    HarmCategory,
    ScreenedDocument,
    ShieldResult,
    TextAnalysis,
)

API_VERSION: Final = "2024-09-01"
COGNITIVE_SERVICES_SCOPE: Final = "https://cognitiveservices.azure.com/.default"
MAX_USER_PROMPT_CHARS: Final = 10_000
MAX_DOCUMENTS_PER_REQUEST: Final = 5
MAX_DOCUMENT_CHARS_PER_REQUEST: Final = 10_000
MAX_ANALYZE_CHARS: Final = 10_000
# Consecutive pieces of an over-long text overlap, so an attack phrase that
# straddles a split point is still seen whole by one request.
SPLIT_OVERLAP_CHARS: Final = 200


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _AttackAnalysis(_Model):
    attackDetected: bool  # noqa: N815  # service field name


class _ShieldResponse(_Model):
    userPromptAnalysis: _AttackAnalysis  # noqa: N815
    documentsAnalysis: list[_AttackAnalysis]  # noqa: N815


class _CategoryAnalysis(_Model):
    category: str
    severity: int


class _AnalyzeResponse(_Model):
    categoriesAnalysis: list[_CategoryAnalysis]  # noqa: N815


def split_text(text: str, limit: int, overlap: int = SPLIT_OVERLAP_CHARS) -> list[str]:
    """Pieces of at most ``limit`` characters covering ``text``, overlapping by ``overlap``."""
    if limit <= overlap:
        raise ValueError("limit must exceed overlap")
    if len(text) <= limit:
        return [text]
    step = limit - overlap
    return [text[start : start + limit] for start in range(0, len(text) - overlap, step)]


def plan_document_batches(
    documents: Sequence[ScreenedDocument],
    *,
    max_documents: int = MAX_DOCUMENTS_PER_REQUEST,
    max_chars: int = MAX_DOCUMENT_CHARS_PER_REQUEST,
) -> list[list[tuple[str, str]]]:
    """Group ``(document_id, piece)`` pairs into requests within the service limits."""
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    size = 0
    for document in documents:
        for piece in split_text(document.text, max_chars):
            if current and (len(current) == max_documents or size + len(piece) > max_chars):
                batches.append(current)
                current, size = [], 0
            current.append((document.document_id, piece))
            size += len(piece)
    if current:
        batches.append(current)
    return batches


async def _all[T](requests: Iterable[Coroutine[Any, Any, T]]) -> list[T]:
    """Run requests concurrently; the first failure cancels the rest.

    The first failure is re-raised on its own (not as an exception group), preferring
    an unexpected error over ``ContentSafetyUnavailableError`` so bugs are not
    disguised as outages. Either way the workflow never receives a verdict.
    """
    tasks: list[asyncio.Task[T]] = []
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(request) for request in requests]
    except ExceptionGroup as failures:
        unavailable, other = failures.split(ContentSafetyUnavailableError)
        first: BaseException = other if other is not None else (unavailable or failures)
        while isinstance(first, BaseExceptionGroup):
            first = first.exceptions[0]
        raise first from failures
    return [task.result() for task in tasks]


class AzureContentSafetyChecker:
    """``ContentSafetyChecker`` backed by an Azure AI Content Safety account."""

    def __init__(
        self,
        endpoint: str,
        credential: AsyncTokenCredential,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("The Content Safety endpoint must use HTTPS.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._endpoint = endpoint.rstrip("/")
        self._credential = credential
        self._timeout = timeout_seconds
        self._client = client if client is not None else httpx.AsyncClient()

    async def close(self) -> None:
        await self._client.aclose()

    async def shield_prompt(
        self,
        user_prompt: str,
        documents: Sequence[ScreenedDocument],
        *,
        deadline_utc: datetime | None = None,
    ) -> ShieldResult:
        prompts = split_text(user_prompt, MAX_USER_PROMPT_CHARS)
        batches: list[list[tuple[str, str]]] = plan_document_batches(documents)
        # Every request carries a prompt piece (the service requires one); extra
        # document batches re-send the last piece, which is harmless.
        count = max(len(prompts), len(batches), 1)
        requests = [
            (
                prompts[min(index, len(prompts) - 1)],
                batches[index] if index < len(batches) else [],
            )
            for index in range(count)
        ]
        replies = await _all(
            self._post(
                "text:shieldPrompt",
                {"userPrompt": prompt, "documents": [piece for _, piece in batch]},
                _ShieldResponse,
                deadline_utc,
            )
            for prompt, batch in requests
        )
        attack = False
        attacked: set[str] = set()
        for (_, batch), reply in zip(requests, replies, strict=True):
            if len(reply.documentsAnalysis) != len(batch):
                raise ContentSafetyUnavailableError("malformed_response")
            attack = attack or reply.userPromptAnalysis.attackDetected
            attacked.update(
                document_id
                for (document_id, _), analysis in zip(batch, reply.documentsAnalysis, strict=True)
                if analysis.attackDetected
            )
        return ShieldResult(
            user_prompt_attack=attack,
            attacked_document_ids=tuple(
                document.document_id for document in documents if document.document_id in attacked
            ),
        )

    async def analyze_text(
        self, text: str, *, deadline_utc: datetime | None = None
    ) -> TextAnalysis:
        replies = await _all(
            self._post(
                "text:analyze",
                {
                    "text": piece,
                    "categories": [category.value for category in HarmCategory],
                    "outputType": "FourSeverityLevels",
                },
                _AnalyzeResponse,
                deadline_utc,
            )
            for piece in split_text(text, MAX_ANALYZE_CHARS)
        )
        severities = dict.fromkeys(HarmCategory, 0)
        seen: set[HarmCategory] = set()
        for reply in replies:
            for item in reply.categoriesAnalysis:
                try:
                    category = HarmCategory(item.category)
                except ValueError:
                    continue  # categories this policy does not request are ignored
                if not 0 <= item.severity <= 7:
                    raise ContentSafetyUnavailableError("malformed_response")
                seen.add(category)
                severities[category] = max(severities[category], item.severity)
        if seen != set(HarmCategory):
            raise ContentSafetyUnavailableError("incomplete_analysis")
        return TextAnalysis(severities=severities)

    def _timeout_for(self, deadline_utc: datetime | None) -> float:
        if deadline_utc is None:
            return self._timeout
        remaining = (deadline_utc - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise ContentSafetyUnavailableError("deadline_elapsed")
        return min(self._timeout, remaining)

    async def _post[ResponseT: _Model](
        self,
        operation: str,
        body: dict[str, Any],
        response_model: type[ResponseT],
        deadline_utc: datetime | None,
    ) -> ResponseT:
        timeout = self._timeout_for(deadline_utc)
        try:
            async with asyncio.timeout(timeout):
                token = await self._credential.get_token(COGNITIVE_SERVICES_SCOPE)
                response = await self._client.post(
                    f"{self._endpoint}/contentsafety/{operation}",
                    params={"api-version": API_VERSION},
                    json=body,
                    headers={"Authorization": f"Bearer {token.token}"},
                    timeout=httpx.Timeout(timeout),
                )
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ContentSafetyUnavailableError("timeout") from error
        except httpx.HTTPError as error:
            raise ContentSafetyUnavailableError("transport_error") from error
        except AzureError as error:  # the managed identity could not issue a token
            raise ContentSafetyUnavailableError("credential_error") from error
        if response.status_code != 200:
            raise ContentSafetyUnavailableError(f"http_{response.status_code}")
        try:
            return response_model.model_validate_json(response.content)
        except ValidationError as error:
            raise ContentSafetyUnavailableError("malformed_response") from error

