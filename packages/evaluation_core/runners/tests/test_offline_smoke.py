"""The smoke gate measures the product: breaking a real control fails its metric."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from accelerator.agent_core.approvals import Approval, canonical_args_hash
from accelerator.agent_core.middleware import ToolPolicyMiddleware
from accelerator.agent_core.tools import IdempotentWriteTool
from accelerator.agent_core.workflows import generation
from accelerator.retrieval_core.citations import SameTurnCitationValidator
from accelerator.retrieval_core.sufficiency import EvidenceSufficiencyChecker
from accelerator.retrieval_core.sufficiency.policy import SufficiencyDecision
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.prompt_injection import WrappedEvidence

from ...datasets import load_dataset
from ..corpus import load_corpus
from ..offline import OfflineModel, OfflineRetriever, ReviewRequestArgs, WriteExecution
from ..smoke import EvaluationResult, GateName, RowOutcome, measure_smoke, write_was_approved

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "evaluations/example-datasets/smoke.jsonl"
CORPUS = ROOT / "tests/fixtures/retrieval/corpus"


def measure() -> tuple[EvaluationResult, tuple[RowOutcome, ...]]:
    return asyncio.run(measure_smoke(load_dataset(DATASET), load_corpus(CORPUS)))


def failed_rows(outcomes: tuple[RowOutcome, ...], gate: GateName) -> set[str]:
    return {outcome.row_id for outcome in outcomes if not outcome.checks[gate]}


def test_the_product_passes_every_gate_on_the_smoke_dataset() -> None:
    result, outcomes = measure()

    assert result.metrics == {gate.value: 1.0 for gate in GateName}
    assert result.hard_failures == ()
    assert all(outcome.error is None for outcome in outcomes)


def test_a_retriever_that_ignores_the_context_scope_fails_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OfflineRetriever.retrieve

    async def leaky(self: OfflineRetriever, request: Any, ctx: Any) -> Any:
        widened = ctx.model_copy(update={"scope_ids": frozenset({"scope-a", "scope-b"})})
        return await original(self, request, widened)

    monkeypatch.setattr(OfflineRetriever, "retrieve", leaky)
    result, outcomes = measure()

    assert "scope_isolation" in result.hard_failures
    assert "smoke-unsupported-002" in failed_rows(outcomes, GateName.SCOPE_ISOLATION)
    assert "smoke-unsupported-002" in failed_rows(outcomes, GateName.ABSTENTION)


def test_evidence_that_escapes_its_element_is_obeyed_and_fails_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unescaped(items: Any, *, boundary: str | None = None) -> WrappedEvidence:
        del boundary
        body = "\n".join(
            f'<evidence chunk_id="{item.chunk_id}" title="{item.document_title}">\n'
            f"{item.text}\n</evidence>"
            for item in items
        )
        return WrappedEvidence(prompt_block=body, boundary="UNWRAPPED-00")

    monkeypatch.setattr(generation, "wrap_untrusted_documents", unescaped)
    result, outcomes = measure()

    assert "injection_followed" in result.hard_failures
    assert "smoke-injection-002" in failed_rows(outcomes, GateName.INJECTION_FOLLOWED)


def test_a_write_executed_without_approval_fails_the_approval_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ToolPolicyMiddleware.invoke

    async def bypass(
        self: ToolPolicyMiddleware[Any, Any], tool: Any, arguments: Any, context: Any, **kw: Any
    ) -> Any:
        if isinstance(tool, IdempotentWriteTool):
            return await tool.execute_approved(arguments, context, execution_id=uuid4())
        return await original(self, tool, arguments, context, **kw)

    monkeypatch.setattr(ToolPolicyMiddleware, "invoke", bypass)
    result, outcomes = measure()

    assert "approval_bypass" in result.hard_failures
    assert failed_rows(outcomes, GateName.APPROVAL_BYPASS) == {"smoke-tool-002"}


@pytest.mark.parametrize(
    ("scope_ids", "expected"),
    [
        (frozenset({"scope-a"}), True),
        (frozenset(), False),
        (frozenset({"scope-a", "scope-b"}), False),
    ],
)
def test_a_write_must_run_under_exactly_one_approved_scope(
    scope_ids: frozenset[str], expected: bool
) -> None:
    args = ReviewRequestArgs(document_title="Access review standard", reason="Annual review.")
    execution = WriteExecution(uuid4(), args, scope_ids)
    approval = Approval(
        id=execution.execution_id,
        tool_name="request_document_review",
        args_hash=canonical_args_hash(args),
        scope_id="scope-a",
        requested_by="evaluation-principal",
        status="executed",
        decided_by="approver",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        correlation_id="c",
    )
    context = ExecutionContext(
        correlation_id="c",
        user_id="evaluation-principal",
        roles=frozenset({"reader"}),
        scope_ids=frozenset({"scope-a", "scope-b"}),
        session_id="s",
        deadline_utc=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert (
        write_was_approved(
            execution, tool_name="request_document_review", context=context, approval=approval
        )
        is expected
    )


def test_unvalidated_fabricated_citations_fail_citation_validity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OfflineModel._answer

    def fabricate(self: OfflineModel, query: Any, evidence: Any) -> str:
        return original(self, query, evidence) + " [cite:fabricated-chunk]"

    monkeypatch.setattr(OfflineModel, "_answer", fabricate)
    monkeypatch.setattr(SameTurnCitationValidator, "validate", lambda *args, **kwargs: None)
    result, _ = measure()

    assert "citation_validity" in result.hard_failures
    assert result.metrics["citation_validity"] < 1.0


def test_a_sufficiency_gate_that_never_abstains_lowers_abstention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_sufficient(self: Any, evidence: Any) -> SufficiencyDecision:
        return SufficiencyDecision(sufficient=True, reason="forced", evidence_ids=[])

    monkeypatch.setattr(EvidenceSufficiencyChecker, "evaluate", always_sufficient)
    result, outcomes = measure()

    assert result.metrics["abstention"] < 1.0
    assert {"smoke-unsupported-001", "smoke-unsupported-002"} <= failed_rows(
        outcomes, GateName.ABSTENTION
    )


def test_a_model_that_never_calls_tools_lowers_tool_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(OfflineModel, "_choose_tool", staticmethod(lambda query, offered: None))
    result, outcomes = measure()

    assert result.metrics["tool_selection"] == pytest.approx(9 / 11)
    assert failed_rows(outcomes, GateName.TOOL_SELECTION) == {"smoke-tool-001", "smoke-tool-002"}
    assert "tool_selection" not in result.hard_failures


def test_a_pending_write_is_an_approval_not_an_execution() -> None:
    _, outcomes = measure()

    [tool_row] = [outcome for outcome in outcomes if outcome.row_id == "smoke-tool-002"]
    assert tool_row.checks[GateName.APPROVAL_BYPASS]
    assert tool_row.checks[GateName.TOOL_SELECTION]


def test_duplicate_or_empty_datasets_are_rejected() -> None:
    rows = load_dataset(DATASET)
    with pytest.raises(ValueError, match="nonempty"):
        asyncio.run(measure_smoke([], load_corpus(CORPUS)))
    with pytest.raises(ValueError, match="unique"):
        asyncio.run(measure_smoke([rows[0], rows[0]], load_corpus(CORPUS)))
