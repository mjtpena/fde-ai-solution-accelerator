"""Assemble the production grounded-answer workflow from Azure adapters.

Azure AI Search retrieval (scope injected from ``ExecutionContext``), Foundry query
embeddings, the threshold sufficiency gate, a tool-less Foundry agent as the answer
generator, and same-turn citation validation.
"""

from collections.abc import Awaitable, Callable
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from agent_framework.foundry import FoundryEmbeddingClient
from azure.core.credentials_async import AsyncTokenCredential
from azure.search.documents.aio import SearchClient

from accelerator.agent_core.agents.factory import AgentConfig, AgentFactory
from accelerator.agent_core.workflows.generation import AgentAnswerGenerator, ChatAgent
from accelerator.agent_core.workflows.grounded_answer import (
    AnswerGenerator,
    CitationValidator,
    GroundedAnswerWorkflow,
    Retriever,
    SufficiencyChecker,
)
from accelerator.configuration.settings import Settings
from accelerator.infrastructure.foundry.agent_runtime import AgentFrameworkFoundryRuntime
from accelerator.infrastructure.foundry.embedder import EmbeddingClient, FoundryQueryEmbedder
from accelerator.infrastructure.search.adapter import AzureSearchRetriever
from accelerator.infrastructure.search.settings import SearchSettings
from accelerator.observability_core import Telemetry
from accelerator.retrieval_core.citations import SameTurnCitationValidator
from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.retrieval_core.sufficiency import (
    EvidenceSufficiencyChecker,
    SufficiencyPolicy,
)
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.telemetry.traced import (
    TracedAnswerGenerator,
    TracedCitationValidator,
    TracedRetriever,
    TracedSufficiencyChecker,
)

GROUNDED_ANSWER_AGENT = "grounded-answer"
GroundedAnswer = GroundedAnswerWorkflow[RetrievalRequest, ExecutionContext, Evidence]


def instructions_directory() -> Path:
    return Path(str(files("accelerator.agent_core.workflows") / "instructions"))


def _no_tools(name: str) -> Any:
    raise ValueError(f"The grounded-answer agent has no tools; got {name!r}.")


def build_azure_grounded_answer(
    settings: Settings,
    credential: AsyncTokenCredential,
    shutdown: list[Callable[[], Awaitable[None]]],
    *,
    telemetry: Telemetry | None = None,
    capture_evaluation_context: bool = False,
) -> GroundedAnswer:
    if (
        not settings.azure_services_configured
        or settings.search_endpoint is None
        or settings.search_index_name is None
        or settings.search_vector_dimensions is None
        or settings.foundry_project_endpoint is None
        or settings.foundry_model_deployment is None
    ):
        raise ValueError("Foundry and Azure AI Search settings are incomplete.")

    search_client = SearchClient(
        endpoint=str(settings.search_endpoint),
        index_name=settings.search_index_name,
        credential=credential,
    )
    shutdown.append(search_client.close)
    embedding_client = FoundryEmbeddingClient(
        project_endpoint=str(settings.foundry_project_endpoint),
        model=settings.foundry_embedding_deployment,
        credential=credential,
    )
    shutdown.append(embedding_client.close)
    retriever = AzureSearchRetriever(
        search_client,
        FoundryQueryEmbedder(
            cast(EmbeddingClient, embedding_client), dimensions=settings.search_vector_dimensions
        ),
        SearchSettings(
            endpoint=settings.search_endpoint,
            index_name=settings.search_index_name,
            vector_dimensions=settings.search_vector_dimensions,
            managed_identity_client_id=settings.managed_identity_client_id or "default",
            semantic_ranking=settings.search_semantic_ranking,
            vector_candidates=settings.search_vector_candidates,
        ),
    )
    runtime = AgentFrameworkFoundryRuntime(
        str(settings.foundry_project_endpoint), credential=credential
    )
    shutdown.append(runtime.close)
    agents = AgentFactory(runtime, _no_tools, instructions_directory())
    agent = agents.create(
        AgentConfig(
            name=GROUNDED_ANSWER_AGENT,
            model=settings.foundry_model_deployment,
            instructions_file="grounded_answer.md",
        )
    )
    top_k = settings.search_top_k
    sufficiency: SufficiencyChecker[Evidence] = EvidenceSufficiencyChecker(
        SufficiencyPolicy(
            minimum_score=settings.sufficiency_min_score,
            minimum_evidence_count=settings.sufficiency_min_evidence,
        ),
        score_field=settings.sufficiency_score_field,
    )
    generator: AnswerGenerator[Evidence] = AgentAnswerGenerator(
        cast(ChatAgent, agent), max_output_tokens=settings.generation_max_output_tokens
    )
    validator: CitationValidator = SameTurnCitationValidator()
    traced_retriever: Retriever[RetrievalRequest, ExecutionContext, Evidence] = retriever
    if telemetry is not None:
        traced_retriever = TracedRetriever(retriever, telemetry)
        sufficiency = TracedSufficiencyChecker(sufficiency, telemetry)
        generator = TracedAnswerGenerator(
            generator, telemetry, model=settings.foundry_model_deployment
        )
        validator = TracedCitationValidator(validator, telemetry)
    return GroundedAnswerWorkflow(
        retriever=traced_retriever,
        sufficiency_checker=sufficiency,
        answer_generator=generator,
        citation_validator=validator,
        retrieval_request_factory=lambda query: RetrievalRequest(query=query, top_k=top_k),
        capture_evaluation_context=capture_evaluation_context,
    )
