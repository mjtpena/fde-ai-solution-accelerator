import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from accelerator.ingestion.composition import compose_consumer
from accelerator.ingestion.infrastructure.skipped_index import SkippedSearchIndex
from accelerator.ingestion.settings import WorkerSettings


def test_blank_compose_values_mean_unset_and_indexing_is_disabled() -> None:
    environment = {"INGESTION_SEARCH_ENDPOINT": "", "INGESTION_VECTOR_DIMENSIONS": ""}
    with patch.dict(os.environ, environment, clear=True):
        settings = WorkerSettings()

    assert settings.search_endpoint is None
    assert not settings.indexing_configured


async def test_unconfigured_development_worker_has_no_consumer() -> None:
    async with compose_consumer(WorkerSettings(environment="development")) as consumer:
        assert consumer is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"storage_connection_string": "UseDevelopmentStorage=true"}, "managed identity"),
        ({"database_auth_mode": "password"}, "managed_identity"),
        ({"search_endpoint": None}, "requires storage"),
        ({"queue_account_url": None}, "requires storage"),
        ({"database_url": "postgresql://worker:secret@db.example.test/accelerator"}, "password"),
        ({"blob_account_url": "http://account.blob.core.windows.net"}, "https"),
        ({"queue_account_url": "http://account.queue.core.windows.net"}, "https"),
        ({"search_endpoint": "http://search.example.test"}, "https"),
        ({"foundry_project_endpoint": "http://foundry.example.test/api/projects/p"}, "https"),
    ],
)
def test_production_requires_managed_identity_and_complete_settings(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "environment": "production",
        "blob_account_url": "https://account.blob.core.windows.net",
        "queue_account_url": "https://account.queue.core.windows.net",
        "database_url": "postgresql://worker@db.example.test/accelerator",
        "database_auth_mode": "managed_identity",
        "search_endpoint": "https://search.example.test",
        "search_index_name": "chunks",
        "vector_dimensions": 1536,
        "foundry_project_endpoint": "https://foundry.example.test/api/projects/p",
        "foundry_embedding_deployment": "embeddings",
    }
    WorkerSettings.model_validate(values)
    values.update(overrides)

    with pytest.raises(ValidationError, match=message):
        WorkerSettings.model_validate(values)


def test_work_and_poison_queues_must_differ() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        WorkerSettings(queue_name="ingestion", poison_queue_name="ingestion")


def test_account_url_storage_needs_both_blob_and_queue_endpoints() -> None:
    values: dict[str, object] = {
        "blob_account_url": "https://account.blob.core.windows.net",
        "database_url": "postgresql://worker@db.example.test/accelerator",
        "search_endpoint": "https://search.example.test",
        "search_index_name": "chunks",
        "vector_dimensions": 1536,
        "foundry_project_endpoint": "https://foundry.example.test/api/projects/p",
        "foundry_embedding_deployment": "embeddings",
    }
    assert not WorkerSettings.model_validate(values).indexing_configured

    values["queue_account_url"] = "https://account.queue.core.windows.net"
    assert WorkerSettings.model_validate(values).indexing_configured


def test_rejected_settings_do_not_echo_the_dsn() -> None:
    with pytest.raises(ValidationError) as raised:
        WorkerSettings.model_validate(
            {
                "environment": "production",
                "database_url": "postgresql://worker:secret-value@db.example.test/accelerator",
            }
        )

    assert "secret-value" not in str(raised.value)


LOCAL_PIPELINE: dict[str, object] = {
    "environment": "test",
    "storage_connection_string": "UseDevelopmentStorage=true",
    "database_url": "postgresql://worker:local@127.0.0.1/accelerator",
    "skip_search_indexing": True,
}


def test_skipped_indexing_runs_the_local_pipeline_without_search_or_foundry() -> None:
    settings = WorkerSettings.model_validate(LOCAL_PIPELINE)

    assert settings.local_pipeline_configured
    assert not settings.indexing_configured


def test_without_the_skip_setting_the_same_values_leave_ingestion_disabled() -> None:
    settings = WorkerSettings.model_validate({**LOCAL_PIPELINE, "skip_search_indexing": False})

    assert not settings.local_pipeline_configured
    assert not settings.indexing_configured


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment": "production"}, "not allowed in production"),
        ({"search_endpoint": "https://search.example.test"}, "cannot be combined"),
        ({"search_index_name": "chunks"}, "cannot be combined"),
        ({"vector_dimensions": 1536}, "cannot be combined"),
        ({"foundry_project_endpoint": "https://foundry.example.test/p"}, "cannot be combined"),
        ({"foundry_embedding_deployment": "embeddings"}, "cannot be combined"),
    ],
)
def test_skipped_indexing_is_refused_in_production_and_next_to_azure_settings(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        WorkerSettings.model_validate({**LOCAL_PIPELINE, **overrides})


async def test_skipped_index_writes_nothing_and_logs_every_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    index = SkippedSearchIndex()
    with caplog.at_level("WARNING", logger="ingestion_worker"):
        await index.upsert_chunks(["a", "b"])
        await index.delete_chunks("doc-1", ["a"])
        await index.delete_document("doc-1")

    assert [record.getMessage() for record in caplog.records] == [
        "search_indexing_skipped"
    ] * 3
    assert [getattr(record, "operation") for record in caplog.records] == [  # noqa: B009
        "upsert",
        "delete_chunks",
        "delete_document",
    ]
