from datetime import UTC, datetime, timedelta

import pytest

from accelerator.retrieval_core.models import RetrievalRequest
from accelerator.retrieval_core.search import build_query
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
        "(scope_id eq 'tenant''a' or scope_id eq 'tenant-b') "
        "and (document_id eq 'doc') and (version eq 'v''1')"
    )
    assert query.text == "policy"
    assert query.top_k == 5


@pytest.mark.parametrize("name", ["scope_id", "scope_ids", "filter", "vector_filter_mode"])
def test_caller_cannot_supply_authorization_or_raw_filter(name: str) -> None:
    with pytest.raises(ValueError, match="Unsupported retrieval filter"):
        build_query(
            RetrievalRequest(query="policy", top_k=5, filters={name: "tenant-b"}), context("tenant-a")
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
        "(scope_id eq 'tenant-a') and (document_id eq ''' or scope_id eq ''tenant-b')"
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
