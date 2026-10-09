"""Proof, at the hosted boundary, that each turn was screened by content safety.

The hosted agent composes its grounded workflow through a configured factory. The
composition hands that factory a ``ScreeningEnforcement.checker`` and wraps the
application with the same enforcement, which records every verdict the checker
gives during a turn and then refuses to release a result the verdicts do not cover:

* every turn needs a clean Prompt Shields verdict on exactly the caller's query;
* an answer needs every citation to be a retrieved chunk that Prompt Shields
  screened and did not flag, and a harm analysis of exactly the answer text that
  the policy does not block.

Content-safety refusals pass unchanged: refusing is always safe. Anything else
that lacks its verdicts, for example a factory that ignored the checker, becomes a
``content_safety_unavailable`` refusal (fail closed).
"""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from accelerator.security_core.content_safety import (
    OUTPUT_BLOCKED,
    PROMPT_ATTACK,
    UNAVAILABLE,
    ContentSafetyChecker,
    ContentSafetyPolicy,
    ContentSafetyUnavailableError,
    ScreenedDocument,
    ShieldResult,
    TextAnalysis,
)

logger = logging.getLogger(__name__)

CONTENT_SAFETY_REFUSAL_CODES: Final[frozenset[str]] = frozenset(
    {PROMPT_ATTACK, OUTPUT_BLOCKED, UNAVAILABLE}
)


@dataclass(slots=True)
class _TurnVerdicts:
    clean_prompts: set[str] = field(default_factory=set)
    clean_documents: set[str] = field(default_factory=set)
    attacked_documents: set[str] = field(default_factory=set)
    analyses: list[tuple[str, TextAnalysis]] = field(default_factory=list)


_current_turn: ContextVar[_TurnVerdicts | None] = ContextVar(
    "hosted_content_safety_turn", default=None
)


class _RecordingChecker:
    """Delegates to the real checker and records each verdict for the current turn."""

    def __init__(self, inner: ContentSafetyChecker) -> None:
        self._inner = inner

    async def shield_prompt(
        self,
        user_prompt: str,
        documents: Sequence[ScreenedDocument],
        *,
        deadline_utc: datetime | None = None,
    ) -> ShieldResult:
        result = await self._inner.shield_prompt(
            user_prompt, documents, deadline_utc=deadline_utc
        )
        verdicts = _current_turn.get()
        if verdicts is not None:
            if not result.user_prompt_attack:
                verdicts.clean_prompts.add(user_prompt)
            attacked = set(result.attacked_document_ids)
            for document in documents:
                if document.document_id in attacked:
                    verdicts.attacked_documents.add(document.document_id)
                else:
                    verdicts.clean_documents.add(document.document_id)
        return result

    async def analyze_text(
        self, text: str, *, deadline_utc: datetime | None = None
    ) -> TextAnalysis:
        analysis = await self._inner.analyze_text(text, deadline_utc=deadline_utc)
        verdicts = _current_turn.get()
        if verdicts is not None:
            verdicts.analyses.append((text, analysis))
        return analysis


class ScreeningEnforcement:
    """The checker to hand the workflow factory, and the check that it was used."""

    def __init__(self, checker: ContentSafetyChecker, policy: ContentSafetyPolicy) -> None:
        self._checker = _RecordingChecker(checker)
        self.policy = policy

    @property
    def checker(self) -> ContentSafetyChecker:
        return self._checker

    @contextmanager
    def turn(self) -> Iterator["ScreenedTurn"]:
        verdicts = _TurnVerdicts()
        token = _current_turn.set(verdicts)
        try:
            yield ScreenedTurn(verdicts, self.policy)
        finally:
            _current_turn.reset(token)


class ScreenedTurn:
    def __init__(self, verdicts: _TurnVerdicts, policy: ContentSafetyPolicy) -> None:
        self._verdicts = verdicts
        self._policy = policy

    def unscreened_reason(
        self,
        query: str,
        *,
        status: str,
        answer: str | None,
        citations: Sequence[str],
        code: str | None,
    ) -> str | None:
        """Why this result may not be released, or ``None`` when it is covered."""
        if status == "abstained" and code in CONTENT_SAFETY_REFUSAL_CODES:
            return None
        verdicts = self._verdicts
        if query not in verdicts.clean_prompts:
            return "prompt_unscreened"
        if status != "answered":
            return None
        if answer is None:
            return "answer_missing"
        if any(
            chunk_id not in verdicts.clean_documents or chunk_id in verdicts.attacked_documents
            for chunk_id in citations
        ):
            return "citation_unscreened"
        analyses = [analysis for text, analysis in verdicts.analyses if text == answer]
        if not analyses:
            return "answer_unscreened"
        try:
            if any(self._policy.blocked_categories(analysis) for analysis in analyses):
                return "answer_blocked"
        except ContentSafetyUnavailableError:
            return "answer_analysis_incomplete"
        return None


__all__ = ["CONTENT_SAFETY_REFUSAL_CODES", "ScreenedTurn", "ScreeningEnforcement"]
