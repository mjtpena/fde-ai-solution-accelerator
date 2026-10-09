from typing import Annotated

from pydantic import Field, HttpUrl, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

from accelerator.retrieval_core.indexing.schema import IndexName


class SearchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AZURE_SEARCH_", extra="forbid", frozen=True)

    endpoint: Annotated[HttpUrl, UrlConstraints(allowed_schemes=["https"])]
    index_name: IndexName
    vector_dimensions: int = Field(ge=2, le=4096)
    managed_identity_client_id: str = Field(min_length=1)
    semantic_ranking: bool = True
    vector_candidates: int = Field(default=50, ge=50, le=1000)
