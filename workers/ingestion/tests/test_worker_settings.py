import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from accelerator.ingestion.composition import compose_consumer
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
