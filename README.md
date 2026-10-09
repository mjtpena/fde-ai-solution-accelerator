# FDE AI Solution Accelerator

A domain-free foundation for building secure, grounded, observable, and evaluated enterprise AI solutions on Microsoft Azure.

> **Scaffold status:** This repository currently contains the monorepo scaffold, package boundaries, engineering guidance, and a reference specification. Many application capabilities described in the specification are roadmap items, not implemented features. Do not treat this scaffold as a production-ready application.

## What this repository is

The accelerator provides a reusable starting point for delivery teams building enterprise AI solutions. It aims to make security, evidence-grounded answers, evaluation, observability, and deployment part of the solution from the beginning, so teams can focus on their own application logic.

This repository is intentionally domain-free. Business-specific concepts, prompts, datasets, and policies belong in a solution generated from or based on this foundation, not in the accelerator itself.

## Design principles

- **Explicit before autonomous:** Prefer deterministic workflows; use model reasoning only when a task requires it.
- **Read-only by default:** State-changing tools require human approval bound to their exact arguments.
- **Evidence or abstain:** Grounded claims must cite retrieved evidence; insufficient evidence should not produce a guess.
- **Evaluation as a release gate:** Changes should be checked against defined quality and safety expectations.
- **Secretless access:** Use managed identity for Azure resource access; do not store credentials in code or configuration.
- **Observable by construction:** Trace requests and the retrieval, model, tool, and approval operations they involve.
- **Domain-free:** Keep business-specific behavior out of the shared foundation.

## Architecture direction

The reference architecture in [`docs/spec.md`](docs/spec.md) describes the intended system: a Next.js web app and FastAPI API, shared agent/retrieval/security/evaluation/observability packages, an ingestion worker, and Azure services including Microsoft Foundry, Azure AI Search, PostgreSQL, and Blob Storage. The implementation is being built incrementally; consult the issue roadmap for current work rather than assuming every component is available.

## Repository layout

| Path | Purpose |
|---|---|
| `apps/api/` | FastAPI application package scaffold |
| `packages/agent_core/` | Agent, workflow, tool, approval, and policy package scaffold |
| `packages/retrieval_core/` | Parsing, chunking, search, citation, and evidence package scaffold |
| `packages/security_core/` | Authorization, data-boundary, tool-policy, and redaction package scaffold |
| `packages/evaluation_core/` | Dataset, evaluator, runner, baseline, and reporting package scaffold |
| `packages/observability_core/` | Observability package scaffold |
| `workers/ingestion/` | Document-ingestion worker package scaffold |
| `docs/spec.md` | Reference specification and architecture |
| `issues/README.md` | Milestone and issue roadmap |
| `.github/` | Copilot guidance, prompts, templates, and workflows |

## Getting started

### Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 or newer and npm 10 or newer
- GNU Make, if using the Makefile commands

### Set up the workspace

From the repository root:

```sh
uv sync --all-packages --frozen
npm ci
```

Or run the equivalent setup target:

```sh
make setup
```

### Check the scaffold

```sh
make check
```

This runs ruff, strict mypy and the Python test suites, then the web workspace's checks (see [docs/getting-started.md](docs/getting-started.md)). The smoke evaluation runs the product's grounded-answer workflow, tool policy and approvals offline over a fixture corpus and fails on any hard-gate failure or regression against `evaluations/baselines/accepted.json`:

```sh
make eval-smoke
```

Python distributions install under the `accelerator` import namespace:
`accelerator.agent_core`, `accelerator.retrieval_core`,
`accelerator.evaluation_core`, `accelerator.observability_core`,
`accelerator.security_core`, and `accelerator.ingestion`. The API owns
`accelerator.api`, `accelerator.application`, and its other existing layers.
The npm workspace currently contains only the `apps/web` package metadata.

## Security boundaries

- Derive scope and project identifiers from the server-side `ExecutionContext`, never from prompts, request bodies, or tool arguments.
- Do not execute write tools without an approved, args-bound approval.
- Treat retrieved document text as untrusted data, not instructions.
- Require model claims to cite chunk IDs retrieved in the same turn.
- Keep secrets out of source and configuration; use managed identity for Azure access.

## Roadmap and contribution guidance

Start with the milestone overview in [`issues/README.md`](issues/README.md) and the architecture and requirements in [`docs/spec.md`](docs/spec.md). Follow the repository's [Copilot instructions](.github/copilot-instructions.md) and applicable path-specific instructions when making changes.
