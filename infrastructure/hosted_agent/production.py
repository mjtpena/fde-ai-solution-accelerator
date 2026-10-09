"""Packaged production composition for trusted context and grounded workflows."""

from collections.abc import Callable
from importlib import import_module
from typing import cast

from azure.identity.aio import ManagedIdentityCredential
from pydantic_settings import BaseSettings, SettingsConfigDict

from accelerator.agent_core.hosting.application import (
    ContextResolver,
    GroundedWorkflow,
    WorkflowHostedApplication,
)
from accelerator.agent_core.hosting.contracts import HostedApplication
from accelerator.agent_core.hosting.screening import ScreeningEnforcement
from accelerator.infrastructure.content_safety import AzureContentSafetyChecker
from infrastructure.hosted_agent.configuration import ContentSafetySettings, RuntimeSettings


class CompositionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOSTED_", extra="forbid")

    context_resolver_factory: str
    grounded_workflow_factory: str


def _load_factory(reference: str, setting: str) -> Callable[..., object]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"{setting} must be configured as module:callable")
    factory: object = getattr(import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError(f"{setting} must refer to a callable")
    return cast(Callable[..., object], factory)


def build_content_safety(settings: ContentSafetySettings) -> ScreeningEnforcement:
    """The Azure AI Content Safety checker, authenticated by managed identity only.

    The account disables key auth; the runtime identity needs Cognitive Services User
    on it (``hostedAgentPrincipalId`` in ``infrastructure/main.bicep``). Every failure
    raises ``ContentSafetyUnavailableError`` and the workflow refuses (fail closed).
    """
    credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    checker = AzureContentSafetyChecker(
        settings.content_safety_endpoint,
        credential,
        timeout_seconds=settings.content_safety_timeout_seconds,
    )
    return ScreeningEnforcement(checker, settings.content_safety_policy)


def create_application(*, screening: ScreeningEnforcement | None = None) -> HostedApplication:
    """Compose platform-provided production adapters; never use fake defaults.

    Content safety is built first, so a missing endpoint stops startup before any
    provider loads. The workflow factory is called with ``content_safety_checker``
    and ``content_safety_policy`` keyword arguments and must screen with them (pass
    them to ``GroundedAnswerWorkflow``); the application then refuses any result
    whose verdicts that checker did not give. ``screening`` is for tests only.
    """
    if screening is None:
        screening = build_content_safety(ContentSafetySettings())
    settings = CompositionSettings()
    resolver = _load_factory(settings.context_resolver_factory, "HOSTED_CONTEXT_RESOLVER_FACTORY")()
    workflow = _load_factory(
        settings.grounded_workflow_factory, "HOSTED_GROUNDED_WORKFLOW_FACTORY"
    )(content_safety_checker=screening.checker, content_safety_policy=screening.policy)
    if not callable(getattr(resolver, "resolve", None)):
        raise TypeError("The context resolver factory must return an object with resolve().")
    if not callable(getattr(workflow, "run", None)):
        raise TypeError("The grounded workflow factory must return an object with run().")
    return WorkflowHostedApplication(
        cast(ContextResolver[object], resolver),
        cast(GroundedWorkflow[object], workflow),
        screening=screening,
    )


def runtime_factory() -> HostedApplication:
    """Fail fast on missing hosted settings before resolving production components."""
    RuntimeSettings()
    return create_application()
