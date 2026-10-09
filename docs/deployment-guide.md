# Deployment guide

This guide deploys the accelerator's dev environment to Azure, loads documents,
runs the full evaluation and promotes to production. Two entry points run the
same stages:

- `make deploy-dev` runs `infrastructure/scripts/deploy-dev.sh` with your Azure
  CLI login;
- `.github/workflows/deploy-dev.yml` runs on every push to `main` with GitHub
  OIDC, and can promote to production on a manual run.

> **Not yet verified against a live subscription.** `infrastructure/README.md`
> records that no Azure deployment, database bootstrap or verification has run
> yet. The only reported evidence is a maintainer's local subscription-level
> what-if (30 `Create`, 9 `Unsupported` changes, the latter not validated).
> Hosted OIDC what-if, private database bootstrap, real image deployment and
> application smoke checks are still outstanding (issue #34). The credential-free
> contract tests compile the templates and mock Azure CLI and `psql`; they do not
> prove provisioning. Treat this guide as the intended procedure and record what
> you actually observe.

## What gets deployed

`infrastructure/main.bicep` targets a subscription and creates the resource
group. Every service has key-based authentication disabled and is reached with a
user-assigned managed identity.

| Resource | Notes |
| --- | --- |
| Container Apps environment (VNet-integrated) | `api` (internal ingress only, port 8000), `web` (public, port 3000), `worker` (no ingress, one minimum replica), manual `migrate` job |
| PostgreSQL Flexible Server 16 | Entra-only authentication, delegated subnet, private DNS zone linked to the VNet, public access disabled |
| Storage account | Containers `incoming` and `documents`; queues `ingestion` and `ingestion-poison`; shared keys off |
| Azure AI Search | Local auth off; semantic ranker plan `searchSemanticSearch` (default `free`); index `chunks` with 1536-dimension vectors |
| Azure AI Content Safety | Kind `ContentSafety`, SKU `contentSafetySkuName` (default `S0`), key auth off; the API screens every chat turn with it ([ADR-0007](adr/0007-content-safety.md)) |
| Microsoft Foundry | Project with a chat deployment (`chat-model`, default `gpt-5-mini` `2025-08-07`) and an embedding deployment (`embedding-model`, default `text-embedding-3-small`) |
| Container registry | Images by immutable digest; each app identity has AcrPull |
| Key Vault, Log Analytics, Application Insights | RBAC vault; App Insights ingestion requires Entra |
| User-assigned identities | One each for api, web, worker and migrator |

Identity grants (see [`infrastructure/README.md`](../infrastructure/README.md#least-privilege-access)):

| Identity | Grants |
| --- | --- |
| api | AcrPull, Search Index Data Reader, Foundry project user, Cognitive Services User on the Content Safety account, Monitoring Metrics Publisher, database role `accelerator_api` |
| worker | AcrPull, Search Index Data Contributor, Foundry project user, Blob Data Reader on `incoming`, Blob Data Contributor on `documents`, Queue Message Processor on `ingestion`, Queue Message Sender on `ingestion-poison`, database role `accelerator_worker` |
| migrator | AcrPull, Monitoring Metrics Publisher, database role `accelerator_migrator` (the only role that can create objects) |
| web | AcrPull only; it reaches the API over the environment's internal network |
| `AZURE_DEPLOYMENT_PRINCIPAL_ID` (optional) | Search Service Contributor, so the `index` stage can create the index |
| `AZURE_EVALUATION_PRINCIPAL_ID` (optional) | Search Index Data Reader, Foundry project access and Cognitive Services User on the Content Safety account, for the full evaluation |

Parameters live in `infrastructure/parameters/dev.example.bicepparam`
(resource group `fde-dev-rg`, prefix `fde-dev`, SKUs, network prefixes). Values
that differ per environment are read from environment variables, listed below.

## Prerequisites

### Tools

| Tool | Needed for |
| --- | --- |
| Azure CLI with Bicep (`az bicep install`) | Every stage; `deploy-dev.sh` adds the `containerapp` extension itself |
| uv and Python 3.12 | `index` stage, full evaluation, hosted agent |
| Git, curl | Deployment name and image tag (git SHA), smoke stage |
| PowerShell 7 and `psql` | Database bootstrap and `validate-deployment.ps1` |

`make deploy-dev` builds images with ACR Tasks, so local Docker is not needed.

### Azure and Entra

1. A subscription and region you are explicitly authorized to deploy into, with
   quota for the chat and embedding models (`AZURE_MODEL_NAME`,
   `AZURE_MODEL_VERSION`, `AZURE_EMBEDDING_MODEL_NAME`,
   `AZURE_EMBEDDING_MODEL_VERSION` override the defaults). No local compile
   proves model availability.
2. **API app registration** exposing a delegated scope and the app roles
   `Reader`, `Contributor`, `Approver`, `Admin`. Its tenant and the token
   audience become `API_ENTRA_TENANT_ID` and `API_ENTRA_AUDIENCE`.
3. **Web SPA app registration** granted that scope. Its client ID and the scope
   become `WEB_ENTRA_CLIENT_ID` and `WEB_ENTRA_API_SCOPE`. Add the deployed web
   URL (the `webUrl` output) as an SPA redirect URI once it is known.
4. **PostgreSQL Entra administrator**: a user, group or service principal
   (`AZURE_POSTGRES_ADMIN_OBJECT_ID`, `AZURE_POSTGRES_ADMIN_NAME`,
   `AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE`). Generic Azure Contributor is not
   database admin.
5. **A machine with private network access** to the PostgreSQL subnet: routing
   to the VNet and resolution of the linked private DNS zone (VNet resolver or
   an approved DNS forwarder), with Azure CLI, PowerShell 7, `psql` and a
   trusted CA bundle for the server certificate, provisioned by your
   administrator. A GitHub-hosted runner cannot reach the database.

### Deployment variables

Used by `deploy-dev.sh` (and the workflow, which reads them from GitHub
environment variables):

| Variable | Required | Purpose |
| --- | --- | --- |
| `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION` | yes | Target subscription and deployment location |
| `AZURE_POSTGRES_ADMIN_OBJECT_ID`, `AZURE_POSTGRES_ADMIN_NAME`, `AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE` | yes | PostgreSQL Entra administrator (principal type defaults to `User` in the parameter file) |
| `API_ENTRA_TENANT_ID`, `API_ENTRA_AUDIENCE` | yes | API token validation; the tenant is also compiled into the web bundle |
| `WEB_ENTRA_CLIENT_ID`, `WEB_ENTRA_API_SCOPE` | yes | Compiled into the web bundle (`NEXT_PUBLIC_*`); not secrets |
| `AZURE_DEPLOYMENT_PRINCIPAL_ID` | no | Your own object ID (or the OIDC principal's), granted Search Service Contributor for the `index` stage |
| `AZURE_DEPLOYMENT_PRINCIPAL_TYPE` | no | Principal type of `AZURE_DEPLOYMENT_PRINCIPAL_ID`: `User` for your own login, default `ServicePrincipal` for the OIDC principal |
| `AZURE_EVALUATION_PRINCIPAL_ID` | no | Object ID of the identity that runs the full evaluation |
| `DEPLOYMENT_NAME` | no | Default `fde-dev-<12-char git SHA>`; the migration and app deployments are `<name>-migrations` and `<name>-apps` |
| `IMAGE_TAG` | no | Default the git SHA |
| `SMOKE_ATTEMPTS`, `SMOKE_INTERVAL_SECONDS` | no | Default 30 attempts, 10 seconds apart |
| `API_IMAGE`, `WEB_IMAGE`, `WORKER_IMAGE` | set by `images` | Immutable `registry/repository@sha256:<digest>` references, written to `.deploy-dev/images.env` |

Never put secrets in these inputs: no keys, passwords or connection strings are
read or written.

## First deployment with `make deploy-dev`

`make deploy-dev` runs every stage (`STAGE=all`), but the database bootstrap is
not part of `all` because it needs private network access. On the first
deployment, run the stages separately with the bootstrap in between. Keep
`DEPLOYMENT_NAME` stable across the stages; by default it changes with every
commit.

```sh
az login
az account set --subscription "$AZURE_SUBSCRIPTION_ID"

export AZURE_SUBSCRIPTION_ID=... AZURE_LOCATION=eastus
export AZURE_POSTGRES_ADMIN_OBJECT_ID=... AZURE_POSTGRES_ADMIN_NAME=... AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE=User
export API_ENTRA_TENANT_ID=... API_ENTRA_AUDIENCE=...
export WEB_ENTRA_CLIENT_ID=... WEB_ENTRA_API_SCOPE=...
export AZURE_DEPLOYMENT_PRINCIPAL_ID="$(az ad signed-in-user show --query id --output tsv)"
export AZURE_DEPLOYMENT_PRINCIPAL_TYPE=User
export DEPLOYMENT_NAME=fde-dev-initial
```

Optionally validate first (what-if with `DEPLOY_APPLICATIONS=false`, or
`-BuildOnly` to compile without Azure access):

```powershell
pwsh infrastructure/scripts/validate-deployment.ps1 `
  -SubscriptionId $env:AZURE_SUBSCRIPTION_ID -Location $env:AZURE_LOCATION `
  -ParameterFile infrastructure/parameters/dev.example.bicepparam `
  -WhatIfOutputPath ./deployment-what-if.json
```

### 1. Infrastructure

```sh
make deploy-dev STAGE=infrastructure
```

Deploys everything except the Container Apps and migration job
(`DEPLOY_APPLICATIONS=false`): registry, network, private PostgreSQL, storage,
Search, Foundry, Key Vault, monitoring, identities and the Container Apps
environment.

### 2. Database bootstrap (once, from the VNet-connected machine)

Read the outputs of the infrastructure deployment:

```sh
for output in postgresFqdn postgresDatabaseName apiIdentityPrincipalId \
  workerIdentityPrincipalId migratorIdentityPrincipalId; do
  printf '%s=%s\n' "$output" "$(az deployment sub show --name "$DEPLOYMENT_NAME" \
    --query "properties.outputs.$output.value" --output tsv)"
done
```

Signed in to the Azure CLI as the PostgreSQL Entra administrator, run the
bootstrap, then the same command with `-VerifyOnly` as the gate:

```powershell
pwsh infrastructure/scripts/bootstrap-postgres.ps1 `
  -HostName $postgresFqdn -DatabaseName $postgresDatabaseName `
  -AdministratorName $env:AZURE_POSTGRES_ADMIN_NAME `
  -ApiPrincipalId $apiIdentityPrincipalId -WorkerPrincipalId $workerIdentityPrincipalId `
  -MigratorPrincipalId $migratorIdentityPrincipalId `
  -CaCertificatePath $trustedCaBundle

pwsh infrastructure/scripts/bootstrap-postgres.ps1 `
  -HostName $postgresFqdn -DatabaseName $postgresDatabaseName `
  -AdministratorName $env:AZURE_POSTGRES_ADMIN_NAME `
  -ApiPrincipalId $apiIdentityPrincipalId -WorkerPrincipalId $workerIdentityPrincipalId `
  -MigratorPrincipalId $migratorIdentityPrincipalId `
  -CaCertificatePath $trustedCaBundle -VerifyOnly
```

The script fetches an Entra token (`az account get-access-token --resource-type
oss-rdbms`), connects with `sslmode=verify-full`, binds `accelerator_api`,
`accelerator_worker` and `accelerator_migrator` to the identities' object IDs
(`pgaadauth_create_principal_with_oid` in the `postgres` database), and grants
CONNECT and schema USAGE, plus schema CREATE for the migrator only. It rejects an
existing mismatched or admin role. Table privileges come later, from the
migration job. Do not run migrations or deploy applications if verification fails.

### 3. Images

```sh
make deploy-dev STAGE=images
```

Builds `api`, `worker` and `web` in the registry with `az acr build`, passing
the Entra tenant, web client ID and API scope as web build arguments, and
writes the digests to `.deploy-dev/images.env` (ignored by Git).

### 4. Migrations

```sh
make deploy-dev STAGE=migrate
```

Deploys the template as `<DEPLOYMENT_NAME>-migrations` with
`DEPLOY_APPLICATIONS=false` and the image digests, which creates or updates only
the migration job (it needs just the API image) and leaves the running revisions
alone. Then starts the `migrate` job (`python -m accelerator.migrations upgrade
head` as `accelerator_migrator`) and polls for up to ten minutes. After upgrading,
the job grants `accelerator_api` and `accelerator_worker` exactly their table
privileges (`accelerator.migrations.grants`). This runs before the applications
stage, so a new revision never serves against an old schema.

### 5. Search index

```sh
make deploy-dev STAGE=index
```

Runs `python -m accelerator.infrastructure.search.provision` with your login,
reading `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME` and
`AZURE_SEARCH_VECTOR_DIMENSIONS` from the infrastructure deployment's outputs,
before any application revision starts. Needs Search
Service Contributor (`AZURE_DEPLOYMENT_PRINCIPAL_ID`).

### 6. Applications

```sh
make deploy-dev STAGE=applications
```

Redeploys the template as `<DEPLOYMENT_NAME>-apps` with
`DEPLOY_APPLICATIONS=true` and the image digests, once the schema and the index
exist. Bicep creates the three Container Apps, attaches a distinct identity to
each, and configures them in production mode (`API_ENVIRONMENT=production`,
`INGESTION_ENVIRONMENT=production`, managed identity for every dependency).

### 7. Smoke

```sh
make deploy-dev STAGE=smoke
```

Requires `GET <webUrl>/` and `GET <webUrl>/api/health` (the web server's
same-origin proxy to the API's `/healthz` over the internal hop) to succeed.

Later deployments, once the database is bootstrapped, can run everything in one
go:

```sh
make deploy-dev
```

## First deployment with GitHub Actions

### GitHub environments and variables

Configure these as environment **variables** (not secrets). Do not store Azure
credentials in GitHub.

**Repository-level**

| Variable | Purpose |
| --- | --- |
| `POSTGRES_RUNNER_LABEL` | Label of the self-hosted VNet runner (`runs-on: [self-hosted, linux, <label>]`) |

**`dev`** (job `deploy_dev` fails fast if any of the first group is missing)

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION` | OIDC deployment principal and target |
| `AZURE_POSTGRES_ADMIN_OBJECT_ID`, `AZURE_POSTGRES_ADMIN_NAME`, `AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE` | PostgreSQL Entra administrator |
| `API_ENTRA_TENANT_ID`, `API_ENTRA_AUDIENCE`, `WEB_ENTRA_CLIENT_ID`, `WEB_ENTRA_API_SCOPE` | Entra app registrations |
| `HOSTED_AGENT_NAME`, `HOSTED_APPLICATION_FACTORY` | Foundry hosted agent |
| `EVALUATION_JUDGE_AZURE_ENDPOINT`, `EVALUATION_JUDGE_AZURE_DEPLOYMENT` | Judge model for the full evaluation |
| `AZURE_DEPLOYMENT_PRINCIPAL_ID` (optional) | The OIDC principal's object ID, for the index stage |
| `AZURE_EVALUATION_PRINCIPAL_ID` (optional) | The VNet runner identity's object ID, for the full evaluation |
| `EVALUATION_DATASET` (optional) | Dataset path for the full evaluation |

**`dev-database`** (protected; used by the VNet runner jobs)

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | OIDC identity of the PostgreSQL Entra administrator |
| `AZURE_POSTGRES_ADMIN_NAME` | Administrator name used as the database user |
| `POSTGRES_CA_CERTIFICATE_PATH` | Path on the runner to the trusted PEM bundle |
| `API_ENTRA_TENANT_ID`, `API_ENTRA_AUDIENCE` | Read by the full evaluation |
| `EVALUATION_PRINCIPAL_OBJECT_ID` | Principal whose scope memberships bound the evaluated turns |
| `EVALUATION_JUDGE_AZURE_ENDPOINT`, `EVALUATION_JUDGE_AZURE_DEPLOYMENT` | Judge model |
| `EVALUATION_DATASET` (optional) | Defaults to `evaluations/example-datasets/full.jsonl` |

**`production`** (add required reviewers before using it)

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | OIDC production principal |
| `AZURE_RESOURCE_GROUP` | Pre-provisioned production resource group |
| `AZURE_WEB_CONTAINER_APP_NAME`, `AZURE_WORKER_CONTAINER_APP_NAME` | Target Container Apps |
| `HOSTED_AGENT_NAME`, `HOSTED_APPLICATION_FACTORY`, `HOSTED_PROJECT_ENDPOINT`, `HOSTED_MODEL_DEPLOYMENT` | Production Foundry project and hosted agent |

### OIDC federated credentials

Add Entra federated credentials with audience `api://AzureADTokenExchange` for
each environment's principal, with subjects:

```text
repo:<owner>/<repo>:environment:dev
repo:<owner>/<repo>:environment:dev-database
repo:<owner>/<repo>:environment:production
```

The workflow header names `mjtpena/fde-ai-solution-accelerator`; use your own
repository. Grant the `dev` principal least-privilege subscription deployment
rights and the right to run ACR Tasks builds (`az acr build`; AcrPush alone is not
enough); the app identities get pull rights from Bicep.

### Self-hosted VNet runner

An ephemeral Linux runner labelled with `POSTGRES_RUNNER_LABEL`, with Azure CLI,
PowerShell 7, `psql`, the trusted PEM, VNet routing and private DNS resolution.
Scope its federation to the `dev-database` environment, sign it in as the
PostgreSQL Entra administrator, and never let untrusted pull-request code onto
it.

### What the workflow runs

On push to `main` (or `workflow_dispatch` on `main`):

| Job | Runner | Does |
| --- | --- | --- |
| `quality` | GitHub-hosted | `make check SKIP_GENERATOR=1`, credential-free Bicep validation, `make eval-smoke` (report uploaded) |
| `deploy_dev` (`dev`) | GitHub-hosted | Checks variables, OIDC sign-in, what-if (artifact uploaded), `deploy-dev.sh infrastructure` (`fde-dev-<sha>`), `deploy-dev.sh images` plus the hosted-agent image, all built with ACR Tasks and recorded by digest, checks provisioning states |
| `verify_database` (`dev-database`) | VNet runner | `bootstrap-postgres.ps1 -VerifyOnly`: verifies, never creates, the database roles |
| `deploy_applications` (`dev`) | GitHub-hosted | What-if, then `deploy-dev.sh migrate` and `index`, then `deploy-dev.sh applications`, provisioning check, `smoke`, then hosted-agent `deploy` and `smoke` |
| `full_evaluation` (`dev-database`) | VNet runner | `make eval-full` against the deployed services |
| `deploy_production` (`production`) | GitHub-hosted | Only on `workflow_dispatch` with `deploy_production: true`; see [Promote to production](#promote-to-production) |

Because `verify_database` only verifies, the database must be bootstrapped by
hand (step 2 above) before the workflow can get past it. On a fresh
subscription, expect the first run to stop at `verify_database`: run the
bootstrap from the VNet machine against the outputs of that run's
`deploy_dev` job, then re-run the failed jobs.

## Ingest documents

There is no upload API yet. Documents are ingested by placing the source file in
the `incoming` container and sending a message to the `ingestion` queue. The
message must come from trusted code or an operator who has already decided which
scope the document belongs to; `scope_id` in the message is trusted as-is.

Message contract (`IngestionMessage` in
`workers/ingestion/src/ingestion_worker/pipeline.py`; unknown fields are
rejected):

| Field | Required | Notes |
| --- | --- | --- |
| `operation` | yes | `ingest` or `delete` |
| `document_id` | yes | 1–255 characters, no control characters |
| `scope_id` | yes | 1–255 characters; the authorization scope of every chunk |
| `content_type` | for `ingest` | `text/plain`, `text/markdown` or `application/pdf` |
| `source_blob` | for `ingest` | Blob name in the `incoming` container |
| `title`, `source_uri`, `version`, `effective_date` | no | Metadata; `effective_date` is `YYYY-MM-DD` |

```json
{
  "operation": "ingest",
  "document_id": "policies/retention.md",
  "scope_id": "scope-a",
  "title": "Retention policy",
  "source_uri": "https://documents.example/retention",
  "version": "3",
  "effective_date": "2026-01-01",
  "content_type": "text/markdown",
  "source_blob": "retention.md"
}
```

The worker reads the message body as plain JSON text. With shared keys disabled,
use Entra authentication. The template does not grant operators data-plane
access to storage, so assign yourself Storage Blob Data Contributor on
`incoming` and a role that can send queue messages on `ingestion` first. For
example:

```sh
account="$(az deployment sub show --name "$DEPLOYMENT_NAME" \
  --query properties.outputs.storageAccountName.value --output tsv)"

az storage blob upload --auth-mode login --account-name "$account" \
  --container-name incoming --name retention.md --file ./retention.md

az storage message put --auth-mode login --account-name "$account" \
  --queue-name ingestion --content "$(cat message.json)"
```

The worker validates, parses and chunks the document (default limit 25 MiB),
stores the canonical copy in `documents`, records chunk lineage in PostgreSQL,
embeds and upserts chunks into Search, removes stale chunks, and sets the
document status to `ready` or `failed(reason)`. Transient failures retry with
backoff up to `INGESTION_MAX_ATTEMPTS` (default 5); invalid messages and rejected
documents go to `ingestion-poison`. `{"operation": "delete", "document_id": ...,
"scope_id": ...}` removes the blob, index entries and database rows.

## Grant scope memberships

Users only retrieve chunks from scopes granted to their Entra object ID in the
`scope_memberships` table (`object_id` varchar(36), `scope_id` varchar(255),
composite primary key). The API role has `SELECT` only and nothing in the
application writes memberships; the repository has no membership-management
tool. Insert them as the PostgreSQL Entra administrator from the VNet machine: each
migration run grants that administrator (`API_DATABASE_OPERATOR_ROLE`, set from
`postgresAdministratorName`) `SELECT, INSERT, DELETE` on `scope_memberships` and
nothing else on application tables (`accelerator.migrations.grants`), so run the
migrations once before this:

```sh
export PGPASSWORD="$(az account get-access-token --resource-type oss-rdbms \
  --query accessToken --output tsv)"
PGSSLMODE=verify-full PGSSLROOTCERT="$trustedCaBundle" \
psql --host "$postgresFqdn" --username "$AZURE_POSTGRES_ADMIN_NAME" \
  --dbname "$postgresDatabaseName" \
  --command "INSERT INTO scope_memberships (object_id, scope_id)
             VALUES ('<user-object-id>', 'scope-a') ON CONFLICT DO NOTHING;"
```

Users also need one of the API app roles (`Reader`, `Contributor`, `Approver`,
`Admin`) assigned in Entra.

## Run the full evaluation

`make eval-full` runs `accelerator.evaluation_core.evaluators` with the API's
own Azure grounded-answer workflow
(`accelerator.infrastructure.evaluation:create_full_evaluation_runtime`) and the
Foundry judge evaluators (groundedness, relevance, retrieval, completeness).
Scopes come from `scope_memberships` for `EVALUATION_PRINCIPAL_OBJECT_ID`; it
refuses to run for a principal with no memberships. Because the database is
private, run it from the VNet machine (the workflow uses the VNet runner).

| Variable | Purpose |
| --- | --- |
| `API_ENVIRONMENT=development`, `API_ENTRA_TENANT_ID`, `API_ENTRA_AUDIENCE` | Settings validation |
| `API_DATABASE_URL`, `API_DATABASE_AUTH_MODE=managed_identity`, `API_DATABASE_TLS_CA_FILE` | Private database, Entra token, trusted CA |
| `API_FOUNDRY_PROJECT_ENDPOINT`, `API_FOUNDRY_MODEL_DEPLOYMENT`, `API_FOUNDRY_EMBEDDING_DEPLOYMENT` | Deployment outputs |
| `API_SEARCH_ENDPOINT`, `API_SEARCH_INDEX_NAME`, `API_SEARCH_VECTOR_DIMENSIONS` | Deployment outputs |
| `API_CONTENT_SAFETY_ENDPOINT` | Deployment output `contentSafetyEndpoint`. Without it a development-mode run is unscreened (logged as `content_safety_unscreened`) |
| `EVALUATION_PRINCIPAL_OBJECT_ID` | Principal whose memberships bound every turn |
| `EVALUATION_JUDGE_AZURE_ENDPOINT`, `EVALUATION_JUDGE_AZURE_DEPLOYMENT` | Judge model; `DefaultAzureCredential`, no API key |
| `EVALUATION_DATASET` | Optional; defaults to `evaluations/example-datasets/full.jsonl` |
| `EVALUATION_GATES` | Optional; defaults to `evaluations/full-gates.yml`, else `evaluations/full-gates.example.yml` |
| `EVALUATION_WORKFLOW_FACTORY` | Optional; a trusted `module:function` |

The workflow connects as the administrator
(`postgresql+asyncpg://<admin-name>@<fqdn>:5432/<database>`), and the signed-in
identity needs Search Index Data Reader and Foundry access
(`AZURE_EVALUATION_PRINCIPAL_ID`) plus inference rights on the judge.

```sh
uv sync --all-packages --frozen
make eval-full
```

With the default dataset, the index must already contain the fixture corpus
(`tests/fixtures/retrieval/corpus/`, scopes per `manifest.json`), ingested
through the worker as above, and the evaluation principal must be a member of
`scope-a` only so the scope-isolation row stays meaningful. The repository has no
script that loads this corpus into a live index. An unconfigured run fails; it
never fabricates scores. The full run produces measured scores, not a
threshold verdict.

## Hosted agent

`infrastructure/hosted_agent/` packages a Foundry hosted agent (Invocations
protocol, port 8088, `/readiness`, `/invocations` accepting only
`{"query": "..."}`). See its
[README](../infrastructure/hosted_agent/README.md).

- `make deploy-dev` does **not** build or deploy it. The workflow builds the
  `fde-agent` image and runs `python -m infrastructure.hosted_agent.cli deploy`
  then `smoke` with `HOSTED_CREDENTIAL_MODE=azure-cli`.
- Configuration comes from `HOSTED_*` variables or a copy of
  `deployment.example.json` passed with `--config`. `HOSTED_CONTEXT_RESOLVER_FACTORY`
  and `HOSTED_GROUNDED_WORKFLOW_FACTORY` are required and must name trusted
  providers packaged in the image. The repository ships only the fail-closed
  production entry point (`infrastructure.hosted_agent.production:runtime_factory`)
  and example placeholders.
- Prerequisites: a region supporting Foundry hosted agents, AcrPull (or
  Container Registry Repository Reader for ABAC registries) for the project's
  managed identity, and Foundry Project Manager for the deployer.

```sh
uv sync --project infrastructure/hosted_agent --frozen
uv run --project infrastructure/hosted_agent python -m infrastructure.hosted_agent.cli deploy --config deployment.json
uv run --project infrastructure/hosted_agent python -m infrastructure.hosted_agent.cli smoke --config deployment.json
```

Deploy creates a version and waits until it is `active`; it does not change an
existing endpoint pin. Smoke invokes the agent endpoint; a grounded abstention
counts as success.

## Promote to production

Production resources are **pre-provisioned separately**; this repository has no
production Bicep parameters. Promotion is a manual workflow run:

1. Run **Deploy dev** with `workflow_dispatch` on `main`, with
   `deploy_production` checked. All dev jobs run first; the earlier jobs only
   run on `refs/heads/main`, so dispatching from another branch skips promotion.
2. `deploy_production` waits for `deploy_dev`, `deploy_applications` and
   `full_evaluation` to succeed, then for the `production` environment's
   reviewers.
3. It validates the production variables, then runs `az containerapp update
   --image` on the **web** and **worker** apps with the exact digests tested in
   dev, and deploys and smoke-tests the hosted agent against
   `HOSTED_PROJECT_ENDPOINT`.

As implemented, promotion does **not** update the production API app, run
production migrations, or create the search index. Do those through your own
approved procedure. The images stay in the dev registry, so the production apps
must be able to pull from it.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `[deploy-dev] error: set <NAME>` | A required variable is unset for that stage |
| `images must be immutable registry/repository@sha256 digests` | Run the `images` stage first, or set `API_IMAGE`, `WEB_IMAGE`, `WORKER_IMAGE` to digest references. `.deploy-dev/images.env` is only used when `API_IMAGE` is unset |
| `az deployment sub show` cannot find the deployment in a later stage | `DEPLOYMENT_NAME` defaults to the current git SHA; a new commit changes it. Export the name used for the `infrastructure` stage |
| Workflow stops at `verify_database` | The bootstrap has not run, the runner cannot resolve or reach the private server, the signed-in identity is not the configured Entra administrator, or the CA path is wrong. The script throws "do not deploy applications" on any verification failure |
| `migration execution ... ended as Failed` | Most likely the bootstrap did not run or bound the wrong object IDs, so `accelerator_migrator` cannot sign in or lacks schema CREATE. List executions with `az containerapp job execution list --name <job> --resource-group <rg> --output table` and read the job's console logs in the Log Analytics workspace (the environment sends all logs there). The job does not retry (`replicaRetryLimit: 0`); fix the cause and re-run the `migrate` stage |
| `smoke test failed` | The web app answered but `/api/health` returned 502 ("The API is unreachable.") or an error, or the web app is not up. The smoke check only proves API liveness (`/healthz`) |
| API revision never becomes ready | The API's readiness probe is `GET /readyz` (internal ingress, so not reachable from outside). It returns 503 with fixed check names: `database` (connection or role privileges, often migrations not run), `identity_provider` (Entra signing keys unreachable), `chat_workflow` (Foundry/Search not configured). Read the reason in the app's logs (`readiness_check_failed`) |
| API exits on start | Production settings validation fails fast when any of the managed identity client ID, database URL, Foundry endpoint and deployments, Search endpoint, index and dimensions, Content Safety endpoint, or Application Insights connection string is missing, or when `API_CONTENT_SAFETY_ENABLED=false` |
| Every chat turn ends in an abstention with code `content_safety_unavailable` | The API cannot reach Azure AI Content Safety and fails closed. Check `fde.content_safety.error_reason` on the `content_safety.*` spans: `http_401`/`http_403` usually means the Cognitive Services User assignment has not propagated yet; `timeout` means the call exceeded `API_CONTENT_SAFETY_TIMEOUT_SECONDS` or the request deadline |
| Worker logs `ingestion_disabled` | Indexing settings are incomplete; in production the worker refuses to start instead |
| Messages land in `ingestion-poison` | Invalid JSON or unknown fields, unsupported `content_type`, oversized, unparseable or empty document, or retries exhausted |
| `403` from Search, Storage, Foundry or the registry right after deployment | Azure role assignments can take several minutes to take effect. Wait and re-run the stage; only `smoke` retries on its own |
| `index` stage gets `403` | `AZURE_DEPLOYMENT_PRINCIPAL_ID` was not set for the infrastructure deployment, or it is not the identity you are signed in as |
| `psql` cannot resolve or reach the PostgreSQL host | Private DNS: the server is only resolvable inside the linked VNet (or through an approved forwarder) and has no public endpoint or firewall exception. Run from the VNet machine |
| TLS verification error to PostgreSQL | Bootstrap uses `verify-full`; supply the correct CA bundle (`-CaCertificatePath`, `POSTGRES_CA_CERTIFICATE_PATH`, `API_DATABASE_TLS_CA_FILE`) |
| Hosted-agent step fails configuration validation in the workflow | The workflow passes `HOSTED_AGENT_NAME`, `HOSTED_APPLICATION_FACTORY`, `HOSTED_PROJECT_ENDPOINT`, `HOSTED_MODEL_DEPLOYMENT`, `HOSTED_IMAGE` and `HOSTED_CREDENTIAL_MODE`, but not the required `HOSTED_CONTEXT_RESOLVER_FACTORY` and `HOSTED_GROUNDED_WORKFLOW_FACTORY`; add them to the step's environment |
| Model deployment fails | Region access or quota; override `AZURE_MODEL_NAME`/`AZURE_MODEL_VERSION` and the embedding equivalents |

For day-two procedures see [`operations-runbook.md`](operations-runbook.md) and
[`handover-checklist.md`](handover-checklist.md).
