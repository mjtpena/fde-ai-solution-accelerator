"""A deterministic Content Safety checker for hosted-agent tests; never packaged."""

from collections.abc import Sequence
from datetime import datetime

from accelerator.agent_core.hosting.screening import ScreeningEnforcement
from accelerator.security_core.content_safety import (
    ContentSafetyPolicy,
    ContentSafetyUnavailableError,
    HarmCategory,
    ScreenedDocument,
    ShieldResult,
    TextAnalysis,
)


class FakeChecker:
    """Prompts containing ATTACK and documents containing INJECT are attacks;
    answers containing HARMFUL are severity 6 violence. ``fail`` raises."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def shield_prompt(
        self,
        user_prompt: str,
        documents: Sequence[ScreenedDocument],
        *,
        deadline_utc: datetime | None = None,
    ) -> ShieldResult:
        self.calls.append("shield_documents" if documents else "shield_prompt")
        if self.fail:
            raise ContentSafetyUnavailableError("timeout")
        return ShieldResult(
            user_prompt_attack="ATTACK" in user_prompt,
            attacked_document_ids=tuple(
                item.document_id for item in documents if "INJECT" in item.text
            ),
        )

    async def analyze_text(
        self, text: str, *, deadline_utc: datetime | None = None
    ) -> TextAnalysis:
        self.calls.append("analyze")
        if self.fail:
            raise ContentSafetyUnavailableError("http_503")
        severity = 6 if "HARMFUL" in text else 0
        return TextAnalysis(dict.fromkeys(HarmCategory, 0) | {HarmCategory.VIOLENCE: severity})


def fake_screening(checker: FakeChecker | None = None) -> ScreeningEnforcement:
    return ScreeningEnforcement(checker or FakeChecker(), ContentSafetyPolicy())
