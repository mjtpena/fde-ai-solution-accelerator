"""Content-safety screening contracts for the grounded-answer workflow (spec §6.2).

The workflow screens three things and never trusts any of them:

* the user's prompt, before retrieval (Prompt Shields user-prompt attack);
* every retrieved chunk, before generation (Prompt Shields document attack);
  flagged chunks are dropped from evidence, never rewritten;
* the generated answer, after citation validation (harm-category severities
  judged by ``ContentSafetyPolicy``).

The adapter (Azure AI Content Safety) lives in the API's ``infrastructure``
package; this module holds only the port, the value types and the policy.
A checker that cannot give a verdict raises ``ContentSafetyUnavailableError``;
callers turn it into a refusal (fail closed), never into an unscreened answer.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol

# Stable reason codes. They reach API clients, audit rows and telemetry.
PROMPT_ATTACK: Final = "content_safety_prompt_attack"
OUTPUT_BLOCKED: Final = "content_safety_output_blocked"
UNAVAILABLE: Final = "content_safety_unavailable"
# Fixed refusal texts: they never echo the prompt, the evidence or the answer.
REFUSAL_REASONS: Final[Mapping[str, str]] = {
    PROMPT_ATTACK: "The request was refused by content safety screening.",
    OUTPUT_BLOCKED: "The generated answer was withheld by content safety screening.",
    UNAVAILABLE: "Content safety screening is unavailable, so no answer was returned.",
}


class HarmCategory(StrEnum):
    """Azure AI Content Safety text categories (``text:analyze``)."""

    HATE = "Hate"
    SELF_HARM = "SelfHarm"
    SEXUAL = "Sexual"
    VIOLENCE = "Violence"


# Severity scale: the service's ``FourSeverityLevels`` output, which reports only
# 0 (safe), 2 (low), 4 (medium) and 6 (high) on the 0-7 range.
SEVERITY_SCALE_MAX: Final = 7
# Block at medium and above, matching the Azure OpenAI default content filter.
DEFAULT_BLOCK_SEVERITY: Final = 4


class ContentSafetyUnavailableError(RuntimeError):
    """The checker could not produce a verdict (error, timeout, malformed reply).

    The message carries only a reason such as ``timeout`` or ``http_503``; never
    screened text.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ScreenedDocument:
    """One untrusted document to shield; ``document_id`` is the chunk ID."""

    document_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ShieldResult:
    user_prompt_attack: bool
    attacked_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TextAnalysis:
    """Severity per harm category, on the 0-7 scale."""

    severities: Mapping[HarmCategory, int]


@dataclass(frozen=True, slots=True)
class ContentSafetyPolicy:
    """Per-category block thresholds: a severity at or above the threshold blocks.

    Thresholds must lie in 1..6 so every category can block something under the
    four-level output (a threshold of 7 would silently disable a category).
    """

    thresholds: Mapping[HarmCategory, int] = field(
        default_factory=lambda: dict.fromkeys(HarmCategory, DEFAULT_BLOCK_SEVERITY)
    )

    def __post_init__(self) -> None:
        if set(self.thresholds) != set(HarmCategory):
            raise ValueError("Every harm category needs a block threshold.")
        if any(not 1 <= value <= SEVERITY_SCALE_MAX - 1 for value in self.thresholds.values()):
            raise ValueError("Block thresholds must be between 1 and 6.")

    def blocked_categories(self, analysis: TextAnalysis) -> tuple[HarmCategory, ...]:
        """Categories at or above their threshold; a missing category is an error."""
        missing = set(HarmCategory) - set(analysis.severities)
        if missing:
            raise ContentSafetyUnavailableError("incomplete_analysis")
        return tuple(
            category
            for category in HarmCategory
            if analysis.severities[category] >= self.thresholds[category]
        )


class ContentSafetyChecker(Protocol):
    async def shield_prompt(
        self,
        user_prompt: str,
        documents: Sequence[ScreenedDocument],
        *,
        deadline_utc: datetime | None = None,
    ) -> ShieldResult:
        """Prompt Shields; raises ``ContentSafetyUnavailableError`` without a verdict."""
        ...

    async def analyze_text(
        self, text: str, *, deadline_utc: datetime | None = None
    ) -> TextAnalysis:
        """Harm-category severities; raises ``ContentSafetyUnavailableError`` without one."""
        ...


__all__ = [
    "DEFAULT_BLOCK_SEVERITY",
    "OUTPUT_BLOCKED",
    "PROMPT_ATTACK",
    "REFUSAL_REASONS",
    "SEVERITY_SCALE_MAX",
    "UNAVAILABLE",
    "ContentSafetyChecker",
    "ContentSafetyPolicy",
    "ContentSafetyUnavailableError",
    "HarmCategory",
    "ScreenedDocument",
    "ShieldResult",
    "TextAnalysis",
]
