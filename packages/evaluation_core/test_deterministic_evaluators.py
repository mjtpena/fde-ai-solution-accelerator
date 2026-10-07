from collections.abc import Callable

import pytest
from pydantic import ValidationError

from .evaluators import (
    evaluate_abstention,
    evaluate_approval_bypass,
    evaluate_citation_validity,
    evaluate_scope_isolation,
    evaluate_tool_selection,
)
from .runners.smoke import (
    EvaluationResult,
    GateName,
    _build_result,
    run_smoke,
)


class _TestExecutionContext:
    def __init__(self, scope_ids: frozenset[str]) -> None:
        self.scope_ids = scope_ids


class _TestApproval:
    def __init__(self, tool_name: str, args_hash: str, scope_id: str, status: str) -> None:
        self.tool_name = tool_name
        self.args_hash = args_hash
        self.scope_id = scope_id
        self.status = status


@pytest.mark.parametrize(
    ("gate", "broken_check"),
    [
        (
            GateName.CITATION_VALIDITY,
            lambda: evaluate_citation_validity({"chunk-1"}, {"fabricated-chunk"}),
        ),
        (GateName.ABSTENTION, lambda: evaluate_abstention(True, False)),
        (GateName.TOOL_SELECTION, lambda: evaluate_tool_selection("search", "write")),
        (
            GateName.SCOPE_ISOLATION,
            lambda: evaluate_scope_isolation(
                _TestExecutionContext(frozenset({"scope-a"})),
                {"scope-a", "scope-attacker"},
            ),
        ),
        (
            GateName.APPROVAL_BYPASS,
            lambda: evaluate_approval_bypass(
                write_executed=True,
                tool_name="write",
                args_hash="call-hash",
                context=_TestExecutionContext(frozenset({"scope-a"})),
                approval=None,
            ),
        ),
    ],
)
def test_broken_fixture_fails_its_hard_gate(
    gate: GateName,
    broken_check: Callable[[], bool],
) -> None:
    assert not broken_check()
    checks = {name: (True,) for name in GateName}
    checks[gate] = (broken_check(),)

    result = _build_result(checks)

    assert result.hard_failures == (gate.value,)
    assert result.metrics[gate.value] == 0.0


def test_scope_widening_input_cannot_override_execution_context() -> None:
    context = _TestExecutionContext(frozenset({"scope-a"}))
    untrusted_input_scope_ids = {"scope-attacker"}
    effective_scope_ids = context.scope_ids | untrusted_input_scope_ids

    assert not evaluate_scope_isolation(context, effective_scope_ids)


def test_args_or_scope_mismatch_cannot_authorize_a_write() -> None:
    context = _TestExecutionContext(frozenset({"scope-a"}))
    approval = _TestApproval("write", "approved-hash", "scope-attacker", "executed")

    assert not evaluate_approval_bypass(
        write_executed=True,
        tool_name="write",
        args_hash="call-hash",
        context=context,
        approval=approval,
    )


def test_invalid_pass_rate_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluationResult(metrics={"citation_validity": 1.1}, hard_failures=())


def test_run_smoke_returns_all_clean_hard_gates() -> None:
    result = run_smoke()

    assert result.metrics == {gate.value: 1.0 for gate in GateName}
    assert result.hard_failures == ()
