# ADR-0001: Microsoft Agent Framework as the only agent SDK

**Status:** Accepted
**Date:** 2026-10-09

## Context

The accelerator needs an agent SDK for two different jobs:

- **Explicit workflows.** The grounded-answer path (spec §6.2) is a fixed graph:
  retrieve, check sufficiency, then either generate and validate citations or
  abstain. Each step must be a typed, testable unit, and routing must not be left to
  the model.
- **Model calls with tools.** The answer generator calls a Foundry model deployment
  and may be offered registered tools for the turn. Every tool call must pass through
  the accelerator's own tool policy and approval service (ADR-0004).

Design principle 5 (spec §1) is one framework per concern. Python has several
overlapping options (Semantic Kernel, AutoGen, LangChain/LangGraph, LlamaIndex).
Running two of them side by side would duplicate tool, memory and tracing
abstractions. Delivery teams would then have to learn both and keep both up to date.

The models and agent runtime are Microsoft Foundry (ADR-0002), and credentials are
Entra ID or managed identity only.

## Decision

Use **Microsoft Agent Framework** (Python) for all agent and workflow code. Do not add
any other agent or RAG framework. `.github/copilot-instructions.md` and `AGENTS.md`
explicitly forbid LangChain, LlamaIndex, Semantic Kernel and AutoGen.

How it is used:

- **Workflows with explicit executors.** `GroundedAnswerWorkflow`
  (`packages/agent_core/workflows/grounded_answer.py`) builds a `WorkflowBuilder` graph
  of `Executor` subclasses (`retrieve`, `sufficiency`, `generate`, `abstain`,
  `citation-validation`). A `add_switch_case_edge_group` with `Case`/`Default` makes
  the sufficiency decision choose between generation and abstention. The workflow
  must produce exactly one `GroundedAnswerResult`; any other output is an error.
- **Agents built from trusted configuration.** `AgentFactory`
  (`packages/agent_core/agents/factory.py`) builds an agent from an `AgentConfig`
  (name, model deployment, a relative Markdown instructions file, and tool IDs) and an
  injected `AgentRuntime`. Instruction paths are lexically confined to the
  instructions directory.
- **Foundry integration in an adapter.** `AgentFrameworkFoundryRuntime`
  (`apps/api/src/accelerator/infrastructure/foundry/agent_runtime.py`) creates
  `agent_framework.Agent` instances over `FoundryChatClient`, using
  `DefaultAzureCredential` or an injected credential. No API key is used. The
  workflow and factory code in `agent_core` does not import Azure SDKs.
- **Tools only through policy.** `as_agent_tool`
  (`packages/agent_core/tools/agent_bridge.py`) wraps each registered `EnterpriseTool`
  as a `FunctionTool`. The wrapper validates arguments against the tool's Pydantic
  model and sends them to the policy invoker. It never runs the tool directly. The
  grounded-answer agent is created without tools (`_no_tools` in
  `apps/api/src/accelerator/infrastructure/grounded_answer.py`). `AgentAnswerGenerator`
  (`packages/agent_core/workflows/generation.py`) offers tools per turn only through
  `tools_for_current_turn()`.

Versions are locked in `uv.lock` (`agent-framework-core` 1.20.0,
`agent-framework-foundry` 1.14.0 at the time of writing). Upgrades go through a
dedicated PR that must pass the evaluation gate (spec §2, version policy).

## Consequences

Positive:

- One Microsoft-native SDK covers workflows, agents, function tools and the Foundry
  chat and embedding clients (`FoundryChatClient`, `FoundryEmbeddingClient`). Its
  identity model matches the rest of the stack.
- The workflow graph is explicit and deterministic around the model call.
  Abstention and citation validation are executors, not prompt instructions. The
  offline smoke evaluation (ADR-0005) runs this same workflow class.
- Agent Framework types do not appear in the domain contracts. The workflow ports
  (`Retriever`, `SufficiencyChecker`, `AnswerGenerator`, `CitationValidator`) are
  protocols, so they can be tested with fakes.

Negative / trade-offs:

- The framework and the Foundry SDKs release frequently, and their APIs are still
  changing. Pinned versions and evaluation-gated upgrade PRs are required, not
  optional.
- Fewer community examples than LangChain or LlamaIndex. Engineers must check
  Microsoft Learn rather than rely on recall (working rule 6 in
  `.github/copilot-instructions.md`).
- The workflow graph is rebuilt on each `run()` call. That is cheap at this size, but
  it is a deliberate simplicity choice, not an optimisation.

## Alternatives considered

- **Semantic Kernel.** Agent Framework is its successor for new agent work (spec §2).
  Starting a new baseline on it would mean a later migration.
- **AutoGen.** Merged into Agent Framework (spec §2). Its multi-agent conversation
  model is not needed for a fixed grounded-answer graph.
- **LangGraph / LangChain.** A valid graph runtime, but it would duplicate what Agent
  Framework already provides. It also adds a second tool and tracing abstraction and
  sits outside the Microsoft-native story. It is forbidden by repository rules.
- **LlamaIndex.** Its retrieval abstractions would hide the Azure AI Search filter and
  citation lineage that ADR-0003 keeps visible. It is forbidden by repository rules.
- **Plain SDK calls with no agent framework.** Possible for one model call, but then
  the accelerator would have to build its own workflow routing, function-tool schema
  generation and streaming.

## References

- `docs/spec.md` §1 (principle 5), §2 (technology decisions), §6.2
- `.github/copilot-instructions.md`, `AGENTS.md`
- `packages/agent_core/workflows/grounded_answer.py`
- `packages/agent_core/workflows/generation.py`
- `packages/agent_core/agents/factory.py`
- `packages/agent_core/tools/agent_bridge.py`
- `apps/api/src/accelerator/infrastructure/foundry/agent_runtime.py`
- `apps/api/src/accelerator/infrastructure/foundry/README.md`
- https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/model-providers/microsoft-foundry
- https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools?tabs=python
