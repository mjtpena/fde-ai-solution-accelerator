"""Packaged production composition for trusted context and grounded workflows."""

from collections.abc import Callable
from importlib import import_module
from typing import cast

from pydantic_settings import BaseSettings, SettingsConfigDict

from accelerator.agent_core.hosting.application import (
    ContextResolver,
    GroundedWorkflow,
    WorkflowHostedApplication,
)
from accelerator.agent_core.hosting.contracts import HostedApplication

from infrastructure.hosted_agent.configuration import RuntimeSettings


class CompositionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOSTED_", extra="forbid")

    context_resolver_factory: str
    grounded_workflow_factory: str


def _load_factory(reference: str, setting: str) -> Callable[[], object]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"{setting} must be configured as module:callable")
    factory: object = getattr(import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError(f"{setting} must refer to a callable")
    return cast(Callable[[], object], factory)


def create_application() -> HostedApplication:
    """Compose platform-provided production adapters; never use fake defaults."""
    settings = CompositionSettings()
    resolver = _load_factory(settings.context_resolver_factory, "HOSTED_CONTEXT_RESOLVER_FACTORY")()
    workflow = _load_factory(
        settings.grounded_workflow_factory, "HOSTED_GROUNDED_WORKFLOW_FACTORY"
    )()
    if not callable(getattr(resolver, "resolve", None)):
        raise TypeError("The context resolver factory must return an object with resolve().")
    if not callable(getattr(workflow, "run", None)):
        raise TypeError("The grounded workflow factory must return an object with run().")
    return WorkflowHostedApplication(
        cast(ContextResolver[object], resolver),
        cast(GroundedWorkflow[object], workflow),
    )


def runtime_factory() -> HostedApplication:
    """Fail fast on missing hosted settings before resolving production components."""
    RuntimeSettings()
    return create_application()
