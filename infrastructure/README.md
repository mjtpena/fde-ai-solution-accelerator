# Dev infrastructure

`main.bicep` targets a subscription and creates the resource group. Core resource
modules compose the identity/RBAC modules from issue #33. No keys, database
passwords, or connection strings are supplied or emitted; every service has local
(key) authentication disabled and is reached with a user-assigned identity.

## What gets deployed

| Resource | Notes |
| --- | --- |
| Container Apps | `api` (internal ingress only), `web` (public), `worker` (no ingress), and a manual `migrate` job |
| PostgreSQL Flexible Server | Entra-only auth, private VNet access, no public endpoint |
| Storage | `incoming` and `documents` containers, `ingestion` and `ingestion-poison` queues, shared keys off |
| Azure AI Search | Local auth off; semantic ranker plan `searchSemanticSearch` (default `free`) |
| Foundry | Chat deployment (`modelName`) and embedding deployment (`embeddingModelName`) |
| Key Vault, Log Analytics, Application Insights | RBAC vault; App Insights ingestion requires Entra |
| User-assigned identities | One each for api, web, worker and migrator |

Search and Foundry SKUs are parameters (`searchSkuName`, `searchReplicaCount`,
`searchPartitionCount`, `foundrySkuName`, `modelSkuName`, `embeddingSkuName`).

### Least-privilege access

| Identity | Grants |
| --- | --- |
| api | AcrPull; Search Index Data Reader; Foundry project user; Monitoring Metrics Publisher; database role `accelerator_api` |
| worker | AcrPull; Search Index Data Contributor; Foundry project user; Blob Data Reader on `incoming`; Blob Data Contributor on `documents`; Queue Message Processor on `ingestion`; Queue Message Sender on `ingestion-poison`; database role `accelerator_worker` |
| migrator | AcrPull; Monitoring Metrics Publisher; database role `accelerator_migrator` |
| web | AcrPull only; it calls the API over the environment's internal network |
| deployer (`deploymentPrincipalId`) | Search Service Contributor, to create the index (optional) |

Database privileges: only `accelerator_migrator` may create objects. After each
upgrade it grants the runtime roles exactly their table privileges
(`accelerator.migrations.grants`): the API gets `SELECT, INSERT` on the
append-only audit tables and no access to ingestion tables; the worker gets the
two ingestion tables only.

Container settings match the application's typed settings (`API_*` and
`INGESTION_*`, see `apps/api/src/accelerator/configuration/settings.py` and
`workers/ingestion/src/ingestion_worker/settings.py`): production mode, managed
identity for every dependency, verified TLS to PostgreSQL, and
`APPLICATIONINSIGHTS_CONNECTION_STRING` for Entra-authenticated telemetry.

## Deployment order

1. Configure an explicitly authorized subscription/region and the non-secret
   `AZURE_POSTGRES_ADMIN_OBJECT_ID`, `AZURE_POSTGRES_ADMIN_NAME`, and
   `AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE` inputs. Run the validator without
   `-BuildOnly`, with `DEPLOY_APPLICATIONS=false`, then deploy the same parameter
   file. This creates the registry, private database/network, managed identities,
   Foundry resources, and Container Apps environment.
2. Build and publish the API, web, and worker images from this repository
   (`apps/api/Dockerfile`, `apps/web/Dockerfile`, `workers/ingestion/Dockerfile`).
   Set `API_IMAGE`, `WEB_IMAGE`, and `WORKER_IMAGE` to trusted, immutable registry
   digest references (`registry/repository@sha256:<digest>`). Images must be pullable by the app
   identities: root grants AcrPull on the newly created registry only. Also set
   `API_ENTRA_TENANT_ID` and `API_ENTRA_AUDIENCE`.
3. From a VNet-connected runner signed in as the configured PostgreSQL Entra
   administrator, run `bootstrap-postgres.ps1` with the root outputs
   `postgresFqdn`, `postgresDatabaseName`, `apiIdentityPrincipalId`,
   `workerIdentityPrincipalId`, and `migratorIdentityPrincipalId`. Supply the trusted CA bundle for `verify-full`.
   Run the same command with `-VerifyOnly` as a required deployment gate.
4. Set `DEPLOY_APPLICATIONS=true`, run authenticated what-if again, then deploy
   the same root/parameter file. Bicep creates all three real Container Apps and
   the migration job, attaches a distinct UAMI and registry pull identity to each,
   and emits `apiInternalUrl` and `webUrl`. The worker has no ingress and one
   minimum replica.
5. Start the migration job (`az containerapp job start`) and wait for it to
   succeed before traffic reaches the new revision.

```powershell
pwsh infrastructure/scripts/validate-deployment.ps1 `
  -SubscriptionId $env:AZURE_SUBSCRIPTION_ID -Location $env:AZURE_LOCATION `
  -ParameterFile infrastructure/parameters/dev.example.bicepparam `
  -WhatIfOutputPath $env:RUNNER_TEMP/deployment-what-if.json

pwsh infrastructure/scripts/bootstrap-postgres.ps1 `
  -HostName $postgresFqdn -DatabaseName $postgresDatabaseName `
  -AdministratorName $env:AZURE_POSTGRES_ADMIN_NAME `
  -ApiPrincipalId $apiIdentityPrincipalId -WorkerPrincipalId $workerIdentityPrincipalId `
  -MigratorPrincipalId $migratorIdentityPrincipalId `
  -CaCertificatePath $trustedCaBundle -VerifyOnly
```

`-BuildOnly` compiles both the template and parameter file without Azure access.
The authenticated path fails on an unset/empty administrator or missing image
inputs. What-if artifacts contain only JSON; named sensitive fields are masked.
The CLI invocation uses `--no-pretty-print --output json` (both are needed) and
temporarily sets UTF-8 output so Windows can encode Unicode principal names.
This name-based masking is defense in depth, not a general-purpose guarantee
against secrets embedded inside arbitrary strings. Never put secrets in these
inputs or upload transcripts.

## `make deploy-dev`

`infrastructure/scripts/deploy-dev.sh` (`make deploy-dev`, or `STAGE=<stage>` for
one stage) is the single deployment path; `.github/workflows/deploy-dev.yml` runs
the same stages after its OIDC sign-in.

| Stage | Does |
| --- | --- |
| `infrastructure` | Subscription deployment with `DEPLOY_APPLICATIONS=false` |
| `images` | Builds api, worker and web in the registry with ACR Tasks (no local Docker) and records their digests |
| `applications` | Deploys the Container Apps and migration job by digest |
| `migrate` | Starts the migration job and waits for it to succeed |
| `index` | Creates or updates the search index from the schema-as-code definition |
| `smoke` | Requires the web page and `/api/health` (the API through the internal hop) to answer |

Set `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION`, the three
`AZURE_POSTGRES_ADMIN_*` values, `API_ENTRA_TENANT_ID`, `API_ENTRA_AUDIENCE`,
`WEB_ENTRA_CLIENT_ID` and `WEB_ENTRA_API_SCOPE`. Set
`AZURE_DEPLOYMENT_PRINCIPAL_ID` to your own object ID so the `index` stage may
create the index. The database bootstrap (below) runs once, between the
`infrastructure` and `applications` stages, from a VNet-connected machine.

The workflow's full evaluation runs on the VNet runner: the evaluation principal's
scopes are read from the private database. Set `AZURE_EVALUATION_PRINCIPAL_ID` to
that runner identity's object ID (it receives Search Index Data Reader and Foundry
project access), and `EVALUATION_PRINCIPAL_OBJECT_ID` to the principal whose scope
memberships bound the evaluated turns.

## Database boundary

PostgreSQL public access is disabled. Its delegated subnet and private DNS zone
are linked to the Container Apps VNet. There is no `0.0.0.0` allow-Azure rule or
public administrator firewall exception. A GitHub-hosted runner without private
network access cannot bootstrap this database. Configure a trusted networked
runner and Entra administrator access; do not claim the bootstrap or its
verification succeeded unless they actually ran.

Bootstrap binds each fixed application role to its exact Entra object ID, rejects
an existing mismatched/admin role, and grants only database CONNECT and schema
USAGE, plus schema CREATE for the migrator alone. It grants no role-management or
blanket table privileges; table privileges come from the migrator after each
upgrade, as described above. Runtime images must implement Entra token renewal
and trusted certificate configuration; provisioning cannot supply that behavior.

The bootstrap invokes `pgaadauth_create_principal_with_oid` in the `postgres`
maintenance database, then grants schema usage in the application database.
Verification checks both scopes, nonadmin mappings and absence of role-management
privileges. Run it on a trusted ephemeral runner with Azure CLI, PowerShell 7,
`psql`, and a trusted CA bundle provisioned by your administrator. That runner
needs routing to the private subnet and resolution of the linked private DNS
zone (via the VNet resolver or an approved DNS forwarder). Its OIDC federation
must be scoped to a protected GitHub environment and signed in as the configured
database Entra administrator; generic Azure Contributor is not database admin.
Do not allow untrusted PR code onto this runner.

When changing an already provisioned public database to the private-only server
configuration, follow Azure's supported network migration process; a successful
template compile does not prove that an in-place transition is supported.

Run credential-free contract tests with:

```powershell
pwsh infrastructure/tests/validate-infrastructure.ps1
```

They compile the real template and parameters, inspect private connectivity/app
identity wiring, and mock Azure CLI/psql for success and fail-closed paths. They
do not prove Azure provisioning or live PostgreSQL Entra behavior.

The example uses `gpt-5-mini` version `2025-08-07`, listed as GA in Microsoft's
[model retirement schedule](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule).
Override `AZURE_MODEL_NAME`/`AZURE_MODEL_VERSION` after checking region access and
quota. No local compile proves model availability in a subscription.

## Outstanding external evidence

A maintainer reported an authenticated local subscription-level what-if against
the current template: 30 changes were `Create` and 9 were `Unsupported`. This is
not hosted OIDC evidence, and the unsupported changes were not validated. No Azure
deployment or database bootstrap/verification has run. Issue #34 owns hosted
authenticated CI invocation and successful artifact upload. The issue remains
incomplete until hosted what-if evidence and private database
bootstrap/verification, real image deployment, and application smoke checks pass.
