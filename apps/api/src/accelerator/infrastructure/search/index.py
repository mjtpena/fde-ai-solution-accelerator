from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    VectorSearch,
    VectorSearchProfile,
)

from accelerator.retrieval_core.indexing.schema import (
    CHUNK_FIELDS,
    SEMANTIC_CONFIGURATION,
    VECTOR_ALGORITHM,
    VECTOR_PROFILE,
    IndexDefinition,
)


def build_index(definition: IndexDefinition) -> SearchIndex:
    fields = []
    for field in CHUNK_FIELDS:
        kind = {
            "string": SearchFieldDataType.String,
            "datetime": SearchFieldDataType.DateTimeOffset,
            "vector": SearchFieldDataType.Collection(SearchFieldDataType.Single),
        }[field.kind]
        fields.append(
            SearchField(
                name=field.name,
                type=kind,
                key=field.key,
                searchable=field.searchable,
                filterable=field.filterable,
                hidden=field.kind == "vector",
                vector_search_dimensions=(
                    definition.vector_dimensions if field.kind == "vector" else None
                ),
                vector_search_profile_name=VECTOR_PROFILE if field.kind == "vector" else None,
            )
        )
    return SearchIndex(
        name=definition.name,
        fields=fields,
        vector_search=VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name=VECTOR_ALGORITHM, parameters=HnswParameters(metric="cosine")
                )
            ],
            profiles=[
                VectorSearchProfile(
                    name=VECTOR_PROFILE, algorithm_configuration_name=VECTOR_ALGORITHM
                )
            ],
        ),
        semantic_search=SemanticSearch(
            default_configuration_name=SEMANTIC_CONFIGURATION,
            configurations=[
                SemanticConfiguration(
                    name=SEMANTIC_CONFIGURATION,
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="document_title"),
                        content_fields=[SemanticField(field_name="text")],
                        keywords_fields=[SemanticField(field_name="section_heading")],
                    ),
                )
            ],
        ),
    )


async def ensure_index(client: SearchIndexClient, definition: IndexDefinition) -> None:
    """Provision through an injected token-authenticated infrastructure client."""
    await client.create_or_update_index(build_index(definition))
