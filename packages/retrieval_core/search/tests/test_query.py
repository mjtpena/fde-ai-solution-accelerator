from datetime import UTC, datetime, timedelta

import pytest

from accelerator.retrieval_core.models import RetrievalRequest
from accelerator.retrieval_core.search import build_query
from accelerator.retrieval_core.search.query import MAX_SCOPE_FILTER_BYTES
from accelerator.security_core.data_boundaries.context import ExecutionContext


def context(*scopes: str) -> ExecutionContext:
    return ExecutionContext(
        correlation_id="search-test",
        user_id="user",
        roles=frozenset({"reader"}),
        scope_ids=frozenset(scopes),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


def test_context_scopes_are_escaped_and_metadata_only_narrows() -> None:
    query = build_query(
        RetrievalRequest.model_validate(
            {"query": "policy", "filters": {"version": "v'1", "document_id": "doc"}}
        ),
        context("tenant-b", "tenant'a"),
    )
    assert query.filter == (
        "(search.in(scope_id, 'tenant''a|tenant-b', '|')) "
        "and (document_id eq 'doc') and (version eq 'v''1')"
    )
    assert query.text == "policy"
    assert query.top_k == 5


@pytest.mark.parametrize("name", ["scope_id", "scope_ids", "filter", "vector_filter_mode"])
def test_caller_cannot_supply_authorization_or_raw_filter(name: str) -> None:
    with pytest.raises(ValueError, match="Unsupported retrieval filter"):
        build_query(
            RetrievalRequest(query="policy", top_k=5, filters={name: "tenant-b"}),
            context("tenant-a"),
        )


def test_empty_context_fails_closed() -> None:
    with pytest.raises(PermissionError, match="authorized scope"):
        build_query(RetrievalRequest(query="policy", top_k=5), context())


def test_metadata_filter_literal_cannot_inject_an_or_expression() -> None:
    query = build_query(
        RetrievalRequest(
            query="policy", top_k=5, filters={"document_id": "' or scope_id eq 'tenant-b"}
        ),
        context("tenant-a"),
    )
    assert query.filter == (
        "(search.in(scope_id, 'tenant-a', '|')) "
        "and (document_id eq ''' or scope_id eq ''tenant-b')"
    )


@pytest.mark.parametrize("value", [None, 2, ["doc"], {"eq": "doc"}])
def test_nonstring_metadata_filters_are_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="requires a string"):
        build_query(
            RetrievalRequest(query="policy", top_k=5, filters={"document_id": value}), context("a")
        )


@pytest.mark.parametrize("text", ["", " ", "*"])
def test_semantic_query_cannot_be_empty(text: str) -> None:
    with pytest.raises(ValueError, match="nonempty"):
        build_query(RetrievalRequest(query=text, top_k=5), context("a"))


def test_many_scopes_use_one_search_in_clause_without_truncation() -> None:
    scopes = [f"tenant-{number:04}" for number in range(2000)]
    query = build_query(RetrievalRequest(query="policy"), context(*scopes))
    assert query.filter == f"(search.in(scope_id, '{'|'.join(scopes)}', '|'))"
    assert " or " not in query.filter


def test_scope_delimiter_is_chosen_without_splitting_scope_ids() -> None:
    query = build_query(RetrievalRequest(query="policy"), context("tenant|one", "tenant two,three"))
    assert query.filter == "(search.in(scope_id, 'tenant two,three;tenant|one', ';'))"


def test_scope_filter_exact_size_boundary_and_utf8_overflow() -> None:
    # Reserve 32 bytes for syntax; values are escaped before reaching the service.
    scope = "a" * (MAX_SCOPE_FILTER_BYTES - 33)
    query = build_query(RetrievalRequest(query="policy"), context(scope))
    assert len(query.filter.encode("utf-8")) <= MAX_SCOPE_FILTER_BYTES
    with pytest.raises(ValueError, match="64 KiB"):
        build_query(RetrievalRequest(query="policy"), context(scope + "a"))
    with pytest.raises(ValueError, match="64 KiB"):
        build_query(RetrievalRequest(query="policy"), context("é" * 32768))


def test_unrepresentable_scope_ids_fail_closed() -> None:
    with pytest.raises(ValueError, match="safe search.in delimiter"):
        build_query(RetrievalRequest(query="policy"), context("scope|,;~^"))
