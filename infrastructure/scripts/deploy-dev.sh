#!/usr/bin/env bash
# Deploy the dev environment end to end, or one stage of it.
#
#   infrastructure/scripts/deploy-dev.sh [all|infrastructure|images|applications|migrate|index|smoke]
#
# Uses the signed-in Azure CLI identity (a developer login locally, OIDC in CI). No
# keys or passwords are read or written. `make deploy-dev` runs `all`.
#
# Required: AZURE_SUBSCRIPTION_ID, AZURE_LOCATION, AZURE_POSTGRES_ADMIN_OBJECT_ID,
# AZURE_POSTGRES_ADMIN_NAME, AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE, API_ENTRA_TENANT_ID,
# API_ENTRA_AUDIENCE, WEB_ENTRA_CLIENT_ID, WEB_ENTRA_API_SCOPE.
# Optional: AZURE_DEPLOYMENT_PRINCIPAL_ID (granted Search Service Contributor),
# DEPLOYMENT_NAME (default fde-dev-<git sha>), IMAGE_TAG (default the git sha),
# SMOKE_ATTEMPTS (default 30), SMOKE_INTERVAL_SECONDS (default 10).
#
# The database bootstrap needs private network access to PostgreSQL, so it is not
# part of `all`: run infrastructure/scripts/bootstrap-postgres.ps1 from a
# VNet-connected machine after the first `infrastructure` stage (see README.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PARAMETERS="$ROOT/infrastructure/parameters/dev.example.bicepparam"
TEMPLATE="$ROOT/infrastructure/main.bicep"
SHA="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-fde-dev-$SHA}"
IMAGE_TAG="${IMAGE_TAG:-$SHA}"
STATE_DIR="${DEPLOY_STATE_DIR:-$ROOT/.deploy-dev}"

log() { printf '[deploy-dev] %s\n' "$*" >&2; }
fail() { printf '[deploy-dev] error: %s\n' "$*" >&2; exit 1; }

require() {
  local name
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || fail "set $name"
  done
}

output() {
  # Read one output of the last subscription deployment.
  az deployment sub show --subscription "$AZURE_SUBSCRIPTION_ID" --name "$1" \
    --query "properties.outputs.$2.value" --output tsv
}

deploy_template() {
  local name="$1"
  az deployment sub create \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --location "$AZURE_LOCATION" \
    --name "$name" \
    --template-file "$TEMPLATE" \
    --parameters "$PARAMETERS" \
    --output none
}

stage_infrastructure() {
  require AZURE_SUBSCRIPTION_ID AZURE_LOCATION AZURE_POSTGRES_ADMIN_OBJECT_ID \
    AZURE_POSTGRES_ADMIN_NAME AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE
  log "deploying shared resources ($DEPLOYMENT_NAME)"
  DEPLOY_APPLICATIONS=false deploy_template "$DEPLOYMENT_NAME"
}

image_digest() {
  az acr repository show --name "$1" --image "$2:$IMAGE_TAG" --query digest --output tsv
}

stage_images() {
  require AZURE_SUBSCRIPTION_ID API_ENTRA_TENANT_ID WEB_ENTRA_CLIENT_ID WEB_ENTRA_API_SCOPE
  local registry server
  registry="$(output "$DEPLOYMENT_NAME" registryName)"
  server="$(output "$DEPLOYMENT_NAME" registryLoginServer)"
  mkdir -p "$STATE_DIR"
  log "building images in $registry (ACR Tasks; no local Docker needed)"
  az acr build --registry "$registry" --image "api:$IMAGE_TAG" \
    --file "$ROOT/apps/api/Dockerfile" "$ROOT" --output none
  az acr build --registry "$registry" --image "worker:$IMAGE_TAG" \
    --file "$ROOT/workers/ingestion/Dockerfile" "$ROOT" --output none
  # NEXT_PUBLIC_* values are compiled into the browser bundle; they are not secrets.
  az acr build --registry "$registry" --image "web:$IMAGE_TAG" \
    --file "$ROOT/apps/web/Dockerfile" \
    --build-arg "NEXT_PUBLIC_ENTRA_TENANT_ID=$API_ENTRA_TENANT_ID" \
    --build-arg "NEXT_PUBLIC_ENTRA_CLIENT_ID=$WEB_ENTRA_CLIENT_ID" \
    --build-arg "NEXT_PUBLIC_ENTRA_API_SCOPE=$WEB_ENTRA_API_SCOPE" \
    "$ROOT" --output none
  {
    echo "API_IMAGE=$server/api@$(image_digest "$registry" api)"
    echo "WORKER_IMAGE=$server/worker@$(image_digest "$registry" worker)"
    echo "WEB_IMAGE=$server/web@$(image_digest "$registry" web)"
  } > "$STATE_DIR/images.env"
  log "image digests written to $STATE_DIR/images.env"
}

load_images() {
  if [[ -z "${API_IMAGE:-}" && -f "$STATE_DIR/images.env" ]]; then
    set -a
    # shellcheck disable=SC1091  # written by the images stage
    source "$STATE_DIR/images.env"
    set +a
  fi
  require API_IMAGE WEB_IMAGE WORKER_IMAGE
  local image
  for image in "$API_IMAGE" "$WEB_IMAGE" "$WORKER_IMAGE"; do
    [[ "$image" =~ ^[^[:space:]]+/[^[:space:]]+@sha256:[a-fA-F0-9]{64}$ ]] \
      || fail "images must be immutable registry/repository@sha256 digests"
  done
}

stage_applications() {
  require AZURE_SUBSCRIPTION_ID AZURE_LOCATION API_ENTRA_TENANT_ID API_ENTRA_AUDIENCE
  load_images
  log "deploying applications ($DEPLOYMENT_NAME-apps)"
  DEPLOY_APPLICATIONS=true API_IMAGE="$API_IMAGE" WEB_IMAGE="$WEB_IMAGE" \
    WORKER_IMAGE="$WORKER_IMAGE" deploy_template "$DEPLOYMENT_NAME-apps"
}

stage_migrate() {
  require AZURE_SUBSCRIPTION_ID
  local deployment="$DEPLOYMENT_NAME-apps" group job execution status
  group="$(output "$deployment" resourceGroupName)"
  job="$(az deployment sub show --subscription "$AZURE_SUBSCRIPTION_ID" --name "$deployment" \
    --query 'properties.outputs.containerAppNames.value.migrations' --output tsv)"
  az extension add --name containerapp --upgrade --only-show-errors --output none
  log "running schema migrations ($job)"
  execution="$(az containerapp job start --name "$job" --resource-group "$group" \
    --query name --output tsv)"
  for _ in $(seq 1 60); do
    status="$(az containerapp job execution show --name "$job" --resource-group "$group" \
      --job-execution-name "$execution" --query properties.status --output tsv)"
    case "$status" in
      Succeeded) log "migrations succeeded"; return 0 ;;
      Failed|Stopped|Degraded) fail "migration execution $execution ended as $status" ;;
    esac
    sleep 10
  done
  fail "migration execution $execution did not finish in time"
}

stage_index() {
  require AZURE_SUBSCRIPTION_ID
  local deployment="$DEPLOYMENT_NAME-apps"
  AZURE_SEARCH_ENDPOINT="$(output "$deployment" searchEndpoint)" \
  AZURE_SEARCH_INDEX_NAME="$(output "$deployment" searchIndexName)" \
  AZURE_SEARCH_VECTOR_DIMENSIONS="$(output "$deployment" searchVectorDimensions)" \
    uv run --all-packages python -m accelerator.infrastructure.search.provision
}

stage_smoke() {
  require AZURE_SUBSCRIPTION_ID
  local web attempts="${SMOKE_ATTEMPTS:-30}" interval="${SMOKE_INTERVAL_SECONDS:-10}"
  web="$(output "$DEPLOYMENT_NAME-apps" webUrl)"
  [[ "$web" == https://* ]] || fail "the deployment has no web URL"
  for _ in $(seq 1 "$attempts"); do
    # The web page, then the API through the web server's internal hop.
    if curl --fail --silent --show-error --max-time 10 --output /dev/null "$web/" \
      && curl --fail --silent --show-error --max-time 10 --output /dev/null "$web/api/health"; then
      log "smoke test passed: $web"
      return 0
    fi
    sleep "$interval"
  done
  fail "smoke test failed: $web"
}

main() {
  local stage="${1:-all}"
  case "$stage" in
    infrastructure) stage_infrastructure ;;
    images) stage_images ;;
    applications) stage_applications ;;
    migrate) stage_migrate ;;
    index) stage_index ;;
    smoke) stage_smoke ;;
    all)
      stage_infrastructure
      stage_images
      stage_applications
      stage_migrate
      stage_index
      stage_smoke
      ;;
    *) fail "unknown stage: $stage" ;;
  esac
}

main "$@"
