#!/usr/bin/env bash
set -euo pipefail

# Reference deployment script, modeled after the forecasting repo deploy_to_azure.sh flow:
# configure env -> provision infra -> ACR cloud-build images -> update Container Apps -> deploy Foundry agent via azd.

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-store-replenishment}"
LOCATION="${LOCATION:-eastus}"
PROJECT_NAME="${PROJECT_NAME:-store-replenishment}"
ENVIRONMENT_NAME="${ENVIRONMENT_NAME:-prod}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-store-replenishment}"
ACR_NAME="${ACR_NAME:-crstorereplenishmentprod}"
LOG_ANALYTICS_WORKSPACE_NAME="${LOG_ANALYTICS_WORKSPACE_NAME:-store-replenishment-law}"
APPLICATION_INSIGHTS_NAME="${APPLICATION_INSIGHTS_NAME:-store-replenishment-appi}"
CONTAINER_APPS_ENVIRONMENT_NAME="${CONTAINER_APPS_ENVIRONMENT_NAME:-store-replenishment-aca-env}"
BACKEND_APP_NAME="${BACKEND_APP_NAME:-store-replenishment-backend}"
FRONTEND_APP_NAME="${FRONTEND_APP_NAME:-store-replenishment-frontend}"

cd "$(dirname "$0")"

PARAMS=(
  location="$LOCATION"
  projectName="$PROJECT_NAME"
  environmentName="$ENVIRONMENT_NAME"
  acrName="$ACR_NAME"
  logAnalyticsWorkspaceName="$LOG_ANALYTICS_WORKSPACE_NAME"
  applicationInsightsName="$APPLICATION_INSIGHTS_NAME"
  containerAppsEnvironmentName="$CONTAINER_APPS_ENVIRONMENT_NAME"
  backendAppName="$BACKEND_APP_NAME"
  frontendAppName="$FRONTEND_APP_NAME"
)

if [[ -n "${FOUNDRY_PROJECT_ENDPOINT:-}" ]]; then PARAMS+=(foundryProjectEndpoint="$FOUNDRY_PROJECT_ENDPOINT"); fi
if [[ -n "${FOUNDRY_MODEL_DEPLOYMENT:-}" ]]; then PARAMS+=(foundryModelDeployment="$FOUNDRY_MODEL_DEPLOYMENT"); fi
if [[ -n "${REPLENISH_AUTH_SECRET:-}" ]]; then PARAMS+=(authTokenSecret="$REPLENISH_AUTH_SECRET"); fi
if [[ -n "${REPLENISH_DEMO_USERNAME:-}" ]]; then PARAMS+=(demoUsername="$REPLENISH_DEMO_USERNAME"); fi
if [[ -n "${REPLENISH_DEMO_PASSWORD:-}" ]]; then PARAMS+=(demoPassword="$REPLENISH_DEMO_PASSWORD"); fi
if [[ -n "${REPLENISH_ADMIN_USERNAME:-}" ]]; then PARAMS+=(adminUsername="$REPLENISH_ADMIN_USERNAME"); fi
if [[ -n "${REPLENISH_ADMIN_PASSWORD:-}" ]]; then PARAMS+=(adminPassword="$REPLENISH_ADMIN_PASSWORD"); fi

az group create --name "$RESOURCE_GROUP" --location "$LOCATION"
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DEPLOYMENT_NAME" \
  --template-file main.bicep \
  --parameters "${PARAMS[@]}"

ACR_NAME=$(az deployment group show --resource-group "$RESOURCE_GROUP" --name "$DEPLOYMENT_NAME" --query "properties.outputs.AZURE_CONTAINER_REGISTRY_NAME.value" -o tsv)
ACR_LOGIN_SERVER=$(az deployment group show --resource-group "$RESOURCE_GROUP" --name "$DEPLOYMENT_NAME" --query "properties.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT.value" -o tsv)
BACKEND_APP=$(az deployment group show --resource-group "$RESOURCE_GROUP" --name "$DEPLOYMENT_NAME" --query "properties.outputs.BACKEND_CONTAINER_APP_NAME.value" -o tsv)
FRONTEND_APP=$(az deployment group show --resource-group "$RESOURCE_GROUP" --name "$DEPLOYMENT_NAME" --query "properties.outputs.FRONTEND_CONTAINER_APP_NAME.value" -o tsv)
MIGRATION_JOB=$(az deployment group show --resource-group "$RESOURCE_GROUP" --name "$DEPLOYMENT_NAME" --query "properties.outputs.MIGRATION_JOB_NAME.value" -o tsv)

az acr build --registry "$ACR_NAME" --image "store-replenishment-backend:$IMAGE_TAG" --platform linux/amd64 --file ../backend/Dockerfile ..
az acr build --registry "$ACR_NAME" --image "store-replenishment-frontend:$IMAGE_TAG" --platform linux/amd64 --file ../frontend/Dockerfile ../frontend

az containerapp registry set --resource-group "$RESOURCE_GROUP" --name "$BACKEND_APP" --server "$ACR_LOGIN_SERVER" --identity system
az containerapp registry set --resource-group "$RESOURCE_GROUP" --name "$FRONTEND_APP" --server "$ACR_LOGIN_SERVER" --identity system
az containerapp job registry set --resource-group "$RESOURCE_GROUP" --name "$MIGRATION_JOB" --server "$ACR_LOGIN_SERVER" --identity system

az containerapp job update --resource-group "$RESOURCE_GROUP" --name "$MIGRATION_JOB" --image "$ACR_LOGIN_SERVER/store-replenishment-backend:$IMAGE_TAG"
MIGRATION_EXECUTION=$(az containerapp job start --resource-group "$RESOURCE_GROUP" --name "$MIGRATION_JOB" --query name -o tsv)
while true; do
  MIGRATION_STATUS=$(az containerapp job execution list \
    --resource-group "$RESOURCE_GROUP" \
    --name "$MIGRATION_JOB" \
    --query "[?name=='$MIGRATION_EXECUTION'].properties.status | [0]" \
    -o tsv)
  case "$MIGRATION_STATUS" in
    Succeeded) break ;;
    Failed|Stopped|Cancelled)
      echo "Database migration job $MIGRATION_EXECUTION finished with status $MIGRATION_STATUS" >&2
      exit 1
      ;;
  esac
  sleep 5
done

az containerapp update --resource-group "$RESOURCE_GROUP" --name "$BACKEND_APP" --image "$ACR_LOGIN_SERVER/store-replenishment-backend:$IMAGE_TAG"
az containerapp update --resource-group "$RESOURCE_GROUP" --name "$FRONTEND_APP" --image "$ACR_LOGIN_SERVER/store-replenishment-frontend:$IMAGE_TAG"

if command -v azd >/dev/null 2>&1; then
  azd env set AZURE_RESOURCE_GROUP "$RESOURCE_GROUP"
  azd env set AZURE_LOCATION "$LOCATION"
fi
