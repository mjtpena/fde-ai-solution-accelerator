import os
from typing import Any, Self
from unittest.mock import AsyncMock, patch

import pytest
from azure.search.documents.indexes.models import SearchIndex
from pydantic import ValidationError

from accelerator.infrastructure.search import provision as provisioning
from accelerator.infrastructure.search.provision import ProvisionSettings, provision


class RecordingIndexClient:
    instances: list["RecordingIndexClient"] = []

    def __init__(self, endpoint: str, credential: object) -> None:
        self.endpoint = endpoint
        self.credential = credential
        self.indexes: list[SearchIndex] = []
        self.closed = False
        RecordingIndexClient.instances.append(self)

    async def create_or_update_index(self, index: SearchIndex) -> SearchIndex:
        self.indexes.append(index)
        return index

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True


async def test_provision_creates_index_from_schema_as_code_with_token_credential() -> None:
    RecordingIndexClient.instances.clear()
    credential = AsyncMock()
    settings = ProvisionSettings.model_validate(
        {"endpoint": "https://search.example.test", "index_name": "chunks", "vector_dimensions": 8}
    )

    with patch.object(provisioning, "SearchIndexClient", RecordingIndexClient):
        await provision(settings, credential)

    [client] = RecordingIndexClient.instances
    assert client.credential is credential
    assert client.closed
    [index] = client.indexes
    assert index.name == "chunks"
    fields = {field.name: field for field in index.fields}
    assert fields["scope_id"].filterable
    assert fields["chunk_id"].key
    assert fields["embedding"].vector_search_dimensions == 8


def test_provision_settings_reject_insecure_endpoints_and_bad_names() -> None:
    environment = {
        "AZURE_SEARCH_ENDPOINT": "http://search.example.test",
        "AZURE_SEARCH_INDEX_NAME": "chunks",
        "AZURE_SEARCH_VECTOR_DIMENSIONS": "8",
    }
    with patch.dict(os.environ, environment, clear=True), pytest.raises(ValidationError):
        ProvisionSettings()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ProvisionSettings.model_validate(
            {"endpoint": "https://s.test", "index_name": "Bad Name", "vector_dimensions": 8}
        )
