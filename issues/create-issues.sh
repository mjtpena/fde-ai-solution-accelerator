#!/usr/bin/env bash
# Creates labels, milestones and issues with the GitHub CLI.
# Usage: ./issues/create-issues.sh   (run from repo root, after `gh auth login`)
set -euo pipefail
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)

gh label create "copilot" --color 1f6feb --force >/dev/null
gh label create "hand-built" --color 8250df --force >/dev/null
gh label create "security-sensitive" --color d73a4a --force >/dev/null
gh label create "docs" --color 0e8a16 --force >/dev/null
gh api "repos/$REPO/milestones" -f title="M1 Engineering foundation" >/dev/null 2>&1 || true
gh api "repos/$REPO/milestones" -f title="M2 Identity and data boundaries" >/dev/null 2>&1 || true
gh api "repos/$REPO/milestones" -f title="M3 Retrieval foundation" >/dev/null 2>&1 || true
gh api "repos/$REPO/milestones" -f title="M4 Agent foundation" >/dev/null 2>&1 || true
gh api "repos/$REPO/milestones" -f title="M5 Evaluation and observability" >/dev/null 2>&1 || true
gh api "repos/$REPO/milestones" -f title="M6 Cloud delivery" >/dev/null 2>&1 || true
gh api "repos/$REPO/milestones" -f title="M7 Generator and handover" >/dev/null 2>&1 || true

gh issue create --title "[M1] Monorepo scaffold (uv workspace + npm)" --milestone "M1 Engineering foundation" --label "copilot,hand-built" --body-file "issues/01-monorepo-scaffold--uv-workspace---npm.md"
gh issue create --title "[M1] FastAPI baseline with typed settings" --milestone "M1 Engineering foundation" --label "copilot,hand-built" --body-file "issues/02-fastapi-baseline-with-typed-settings.md"
gh issue create --title "[M1] Next.js baseline with generated API client" --milestone "M1 Engineering foundation" --label "copilot,hand-built" --body-file "issues/03-next-js-baseline-with-generated-api-client.md"
gh issue create --title "[M1] Docker Compose local stack" --milestone "M1 Engineering foundation" --label "copilot,hand-built" --body-file "issues/04-docker-compose-local-stack.md"
gh issue create --title "[M1] Quality tooling and pre-commit" --milestone "M1 Engineering foundation" --label "copilot,hand-built" --body-file "issues/05-quality-tooling-and-pre-commit.md"
gh issue create --title "[M1] Pull-request CI workflow" --milestone "M1 Engineering foundation" --label "copilot,hand-built" --body-file "issues/06-pull-request-ci-workflow.md"
gh issue create --title "[M1] Copilot customisation verified" --milestone "M1 Engineering foundation" --label "copilot,hand-built" --body-file "issues/07-copilot-customisation-verified.md"
gh issue create --title "[M2] Entra ID authentication (web + API)" --milestone "M2 Identity and data boundaries" --label "copilot,security-sensitive" --body-file "issues/08-entra-id-authentication--web---api.md"
gh issue create --title "[M2] Server-side scope resolver" --milestone "M2 Identity and data boundaries" --label "copilot,security-sensitive" --body-file "issues/09-server-side-scope-resolver.md"
gh issue create --title "[M2] Append-only audit events" --milestone "M2 Identity and data boundaries" --label "copilot" --body-file "issues/10-append-only-audit-events.md"
gh issue create --title "[M3] Document, Chunk, Evidence contracts" --milestone "M3 Retrieval foundation" --label "copilot" --body-file "issues/11-document--chunk--evidence-contracts.md"
gh issue create --title "[M3] Parser and chunking interfaces" --milestone "M3 Retrieval foundation" --label "copilot" --body-file "issues/12-parser-and-chunking-interfaces.md"
gh issue create --title "[M3] Idempotent ingestion worker" --milestone "M3 Retrieval foundation" --label "copilot" --body-file "issues/13-idempotent-ingestion-worker.md"
gh issue create --title "[M3] Azure AI Search index and adapter" --milestone "M3 Retrieval foundation" --label "copilot,security-sensitive" --body-file "issues/14-azure-ai-search-index-and-adapter.md"
gh issue create --title "[M3] Evidence sufficiency and abstention" --milestone "M3 Retrieval foundation" --label "copilot" --body-file "issues/15-evidence-sufficiency-and-abstention.md"
gh issue create --title "[M3] Citation validation" --milestone "M3 Retrieval foundation" --label "copilot,security-sensitive" --body-file "issues/16-citation-validation.md"
gh issue create --title "[M3] Retrieval diagnostics endpoint" --milestone "M3 Retrieval foundation" --label "copilot" --body-file "issues/17-retrieval-diagnostics-endpoint.md"
gh issue create --title "[M4] Agent factory (Microsoft Agent Framework + Foundry)" --milestone "M4 Agent foundation" --label "copilot" --body-file "issues/18-agent-factory--microsoft-agent-framework---foundry.md"
gh issue create --title "[M4] EnterpriseTool base and registry" --milestone "M4 Agent foundation" --label "copilot" --body-file "issues/19-enterprisetool-base-and-registry.md"
gh issue create --title "[M4] Tool policy middleware" --milestone "M4 Agent foundation" --label "copilot,security-sensitive" --body-file "issues/20-tool-policy-middleware.md"
gh issue create --title "[M4] Args-bound approval service" --milestone "M4 Agent foundation" --label "copilot,security-sensitive" --body-file "issues/21-args-bound-approval-service.md"
gh issue create --title "[M4] Explicit workflow base + grounded-answer workflow" --milestone "M4 Agent foundation" --label "copilot" --body-file "issues/22-explicit-workflow-base---grounded-answer-workflow.md"
gh issue create --title "[M4] Streaming chat endpoint and UI" --milestone "M4 Agent foundation" --label "copilot" --body-file "issues/23-streaming-chat-endpoint-and-ui.md"
gh issue create --title "[M4] Foundry hosted-agent packaging" --milestone "M4 Agent foundation" --label "copilot" --body-file "issues/24-foundry-hosted-agent-packaging.md"
gh issue create --title "[M5] OpenTelemetry instrumentation" --milestone "M5 Evaluation and observability" --label "copilot" --body-file "issues/25-opentelemetry-instrumentation.md"
gh issue create --title "[M5] Redaction middleware" --milestone "M5 Evaluation and observability" --label "copilot,security-sensitive" --body-file "issues/26-redaction-middleware.md"
gh issue create --title "[M5] Dataset schema and loader" --milestone "M5 Evaluation and observability" --label "copilot" --body-file "issues/27-dataset-schema-and-loader.md"
gh issue create --title "[M5] Foundry evaluator adapters" --milestone "M5 Evaluation and observability" --label "copilot" --body-file "issues/28-foundry-evaluator-adapters.md"
gh issue create --title "[M5] Deterministic evaluators and hard gates" --milestone "M5 Evaluation and observability" --label "copilot,security-sensitive" --body-file "issues/29-deterministic-evaluators-and-hard-gates.md"
gh issue create --title "[M5] Baseline comparison and PR report" --milestone "M5 Evaluation and observability" --label "copilot" --body-file "issues/30-baseline-comparison-and-pr-report.md"
gh issue create --title "[M6] Bicep modules" --milestone "M6 Cloud delivery" --label "copilot" --body-file "issues/31-bicep-modules.md"
gh issue create --title "[M6] Managed identities and RBAC" --milestone "M6 Cloud delivery" --label "copilot,security-sensitive" --body-file "issues/32-managed-identities-and-rbac.md"
gh issue create --title "[M6] GitHub OIDC deploy workflow" --milestone "M6 Cloud delivery" --label "copilot" --body-file "issues/33-github-oidc-deploy-workflow.md"
gh issue create --title "[M6] Security scanning" --milestone "M6 Cloud delivery" --label "copilot" --body-file "issues/34-security-scanning.md"
gh issue create --title "[M6] Cost guardrails" --milestone "M6 Cloud delivery" --label "copilot" --body-file "issues/35-cost-guardrails.md"
gh issue create --title "[M7] Project generator" --milestone "M7 Generator and handover" --label "copilot" --body-file "issues/36-project-generator.md"
gh issue create --title "[M7] Engagement templates" --milestone "M7 Generator and handover" --label "copilot,docs" --body-file "issues/37-engagement-templates.md"
gh issue create --title "[M7] Runbook and handover checklist" --milestone "M7 Generator and handover" --label "copilot,docs" --body-file "issues/38-runbook-and-handover-checklist.md"
gh issue create --title "[M7] Release v0.1.0" --milestone "M7 Generator and handover" --label "copilot" --body-file "issues/39-release-v0-1-0.md"
echo "Done."
