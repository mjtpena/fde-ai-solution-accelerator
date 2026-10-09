# ADR-0006: Azure Container Apps for the API, web, worker and migration job

**Status:** Accepted
**Date:** 2026-10-09

## Context

The accelerator runs four kinds of workload:

| Workload | Ingress | Notes |
| --- | --- | --- |
| API (FastAPI) | Must not be public | The web server calls it |
| Web (Next.js, standalone output) | Public | Serves the UI |
| Ingestion worker | None | Long-running queue consumer |
| Schema migrations | None | Run once per release, under the only identity allowed to change the schema |

Spec §7 requires a user-assigned managed identity per container app with
least-privilege RBAC, no keys, and no public PostgreSQL endpoint. The platform should
be simple enough for a client team to run after handover. It also needs to support
VNet integration, deploy by image digest, and be fully described in Bicep (spec §2).

The Foundry hosted agent (ADR-0002) runs in Foundry's managed runtime, not on this
platform.

## Decision

Host all four workloads in **one Azure Container Apps environment**
(`infrastructure/modules/container-apps.bicep`), deployed by
`infrastructure/main.bicep`.

- **Environment.** Consumption workload profile, logs to Log Analytics. It is
  integrated with the VNet through a delegated `container-apps` subnet
  (`infrastructure/modules/network.bicep`).
- **api.** Port 8000 with **internal ingress only** (`external: false`). It is
  reachable at `https://<api>.internal.<domain>` from inside the environment. Liveness
  probe `/healthz`, readiness probe `/readyz`. Minimum 1 replica (`apiMinReplicas`),
  maximum 3. The image (`apps/api/Dockerfile`) is a multi-stage uv build with
  digest-pinned base images, runs as non-root UID 10001, and starts uvicorn with the
  app factory.
- **web.** Port 3000 with **public ingress**, minimum 0 replicas (scale to zero),
  maximum 3. `API_BASE_URL` points at the API's internal URL. Readiness goes through
  the internal hop; liveness does not.
- **worker.** No ingress, exactly 1 replica.
- **migrate.** A **manual Container Apps job** that runs the API image with
  `python -m accelerator.migrations upgrade head`. It is started and awaited before
  traffic reaches a new revision.
- **Identity.** api, web, worker and migrator each get a distinct **user-assigned
  identity**, which is also used for the AcrPull registry pull. The RBAC table in
  `infrastructure/README.md` lists each identity's grants. The web identity has
  AcrPull only. The API has Search Index Data Reader and Foundry access. The worker
  has Search Index Data Contributor, blob and queue roles, and Foundry access.
- **Database.** PostgreSQL Flexible Server is **Entra-only and private**: public
  access is disabled, it uses a delegated subnet, and its private DNS zone is linked
  to the Container Apps VNet. Database roles are bound to each identity's object ID
  by `infrastructure/scripts/bootstrap-postgres.ps1`.
- **Other services.** Search, Storage, Foundry, Key Vault, ACR and monitoring keep
  public network access, with local or key authentication disabled. They are reached
  with Entra tokens.
- **Delivery.** `.github/workflows/deploy-dev.yml` (GitHub OIDC) and
  `infrastructure/scripts/deploy-dev.sh` (`make deploy-dev`) share the same stages:
  infrastructure, images, applications by digest, migrate, index, smoke. Revisions
  use `activeRevisionsMode: Single`.

## Consequences

Positive:

- Container hosting without managing a cluster: ingress, TLS, revisions, probes and
  scale rules are declared in about 330 lines of Bicep.
- Internal ingress keeps the API off the internet. Only the web app is public, which
  matches spec §3 ("Web never calls models or Search directly").
- A separate identity per app gives a small blast radius. Only the migrator identity
  can change the schema.
- The same images run locally (`docker-compose.yml`) and in Azure.

Negative / trade-offs:

- Only PostgreSQL is network-private. Search, Storage, Foundry, Key Vault and ACR are
  protected by Entra auth only, not by private endpoints. Clients with a "no public
  endpoints" policy must add private endpoints themselves.
- Scaling is minimal. The worker is fixed at one replica, with no queue-length (KEDA)
  rule. The API keeps one warm replica, so only the web app scales to zero. The
  spec §2 "scale-to-zero" rationale applies only to the web app.
- The PostgreSQL bootstrap needs a VNet-connected runner signed in as the database
  Entra administrator. A GitHub-hosted runner cannot do it.
- Fixed container sizes (0.5 vCPU / 1 GiB) and a maximum of 3 replicas are baseline
  values, not tuned numbers.
- `infrastructure/README.md` ("Outstanding external evidence") records that no full
  Azure deployment, database bootstrap or application smoke has yet been run from
  hosted CI. Credential-free tests compile the templates and check the wiring
  (`infrastructure/tests/`), but they do not prove provisioning.

## Alternatives considered

- **Azure Kubernetes Service.** Full control over networking and scaling, but cluster
  operations, upgrades and a much larger Bicep and Helm surface are more than a
  four-workload baseline needs (spec §2: "overkill for baseline").
- **Azure App Service.** Hosts the API and web well. However, the long-running worker
  and the one-shot migration job would need WebJobs or a second service, and an
  internal-only API next to a public web app needs extra networking setup. Container
  Apps covers all four workloads with one model.
- **Azure Functions.** A natural fit for queue-triggered ingestion, but a poor fit for
  a streaming FastAPI app and a Next.js server. Splitting hosting models would make
  identity, logging and deployment inconsistent.
- **Host the API inside the Foundry hosted agent runtime.** That runtime serves the
  Invocations protocol for one agent (ADR-0002). It is not a general web host, and it
  does not run the API's approvals, audit or chat endpoints.

## References

- `docs/spec.md` §2 (Hosting, IaC, CI/CD), §3, §7 (identity model)
- `infrastructure/main.bicep`, `infrastructure/modules/container-apps.bicep`,
  `infrastructure/modules/network.bicep`, `infrastructure/modules/postgres.bicep`
- `infrastructure/README.md`
- `infrastructure/scripts/deploy-dev.sh`, `infrastructure/scripts/bootstrap-postgres.ps1`
- `apps/api/Dockerfile`, `apps/web/Dockerfile`, `workers/ingestion/Dockerfile`
- `.github/workflows/deploy-dev.yml`
