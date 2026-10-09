"""Create or update the chunk index from the schema-as-code definition.

Run by the deployment identity (Search Service Contributor), never by the API:

    python -m accelerator.infrastructure.search.provision

Reads ``AZURE_SEARCH_ENDPOINT``, ``AZURE_SEARCH_INDEX_NAME`` and
``AZURE_SEARCH_VECTOR_DIMENSIONS``. Authenticates with ``DefaultAzureCredential``
(workload identity in CI, a developer login locally); no admin keys are accepted.
"""

import asyncio
import logging
from typing import Annotated

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.indexes.aio import SearchIndexClient
from pydantic import Field, HttpUrl, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

from accelerator.infrastructure.search.index import ensure_index
from accelerator.retrieval_core.indexing.schema import IndexDefinition, IndexName

logger = logging.getLogger(__name__)


class ProvisionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AZURE_SEARCH_", extra="ignore", frozen=True)

    endpoint: Annotated[HttpUrl, UrlConstraints(allowed_schemes=["https"])]
    index_name: IndexName
    vector_dimensions: int = Field(ge=2, le=4096)


async def provision(settings: ProvisionSettings, credential: AsyncTokenCredential) -> None:
    definition = IndexDefinition(
        name=settings.index_name, vector_dimensions=settings.vector_dimensions
    )
    async with SearchIndexClient(str(settings.endpoint), credential) as client:
        await ensure_index(client, definition)
    logger.info("search_index_provisioned", extra={"index_name": settings.index_name})


async def _main() -> None:
    settings = ProvisionSettings()  # type: ignore[call-arg]  # values come from the environment
    async with DefaultAzureCredential() as credential:
        await provision(settings, credential)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(_main())
