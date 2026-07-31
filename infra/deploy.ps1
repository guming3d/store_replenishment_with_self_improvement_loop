param(
    [string]$ResourceGroup = $env:RESOURCE_GROUP,
    [string]$Location = $env:LOCATION,
    [string]$ProjectName = $env:PROJECT_NAME,
    [string]$EnvironmentName = $env:ENVIRONMENT_NAME,
    [string]$ImageTag = $env:IMAGE_TAG,
    [string]$DeploymentName = $env:DEPLOYMENT_NAME
)

# Reference deployment script, modeled after the forecasting repo deploy_to_azure.sh flow:
# configure env -> provision infra -> ACR cloud-build images -> update Container Apps -> deploy Foundry agent via azd.

$ErrorActionPreference = 'Stop'
if (-not $ResourceGroup) { $ResourceGroup = 'rg-store-replenishment' }
if (-not $Location) { $Location = 'eastus' }
if (-not $ProjectName) { $ProjectName = 'store-replenishment' }
if (-not $EnvironmentName) { $EnvironmentName = 'prod' }
if (-not $ImageTag) { $ImageTag = Get-Date -Format 'yyyyMMddHHmmss' }
if (-not $DeploymentName) { $DeploymentName = 'store-replenishment' }
$AcrNameParam = if ($env:ACR_NAME) { $env:ACR_NAME } else { 'crstorereplenishmentprod' }
$LogAnalyticsWorkspaceName = if ($env:LOG_ANALYTICS_WORKSPACE_NAME) { $env:LOG_ANALYTICS_WORKSPACE_NAME } else { 'store-replenishment-law' }
$ApplicationInsightsName = if ($env:APPLICATION_INSIGHTS_NAME) { $env:APPLICATION_INSIGHTS_NAME } else { 'store-replenishment-appi' }
$ContainerAppsEnvironmentName = if ($env:CONTAINER_APPS_ENVIRONMENT_NAME) { $env:CONTAINER_APPS_ENVIRONMENT_NAME } else { 'store-replenishment-aca-env' }
$BackendAppName = if ($env:BACKEND_APP_NAME) { $env:BACKEND_APP_NAME } else { 'store-replenishment-backend' }
$FrontendAppName = if ($env:FRONTEND_APP_NAME) { $env:FRONTEND_APP_NAME } else { 'store-replenishment-frontend' }

Set-Location $PSScriptRoot

$parameters = @(
    "location=$Location",
    "projectName=$ProjectName",
    "environmentName=$EnvironmentName",
    "acrName=$AcrNameParam",
    "logAnalyticsWorkspaceName=$LogAnalyticsWorkspaceName",
    "applicationInsightsName=$ApplicationInsightsName",
    "containerAppsEnvironmentName=$ContainerAppsEnvironmentName",
    "backendAppName=$BackendAppName",
    "frontendAppName=$FrontendAppName"
)
if ($env:FOUNDRY_PROJECT_ENDPOINT) { $parameters += "foundryProjectEndpoint=$($env:FOUNDRY_PROJECT_ENDPOINT)" }
if ($env:FOUNDRY_MODEL_DEPLOYMENT) { $parameters += "foundryModelDeployment=$($env:FOUNDRY_MODEL_DEPLOYMENT)" }
if ($env:REPLENISH_AUTH_SECRET) { $parameters += "authTokenSecret=$($env:REPLENISH_AUTH_SECRET)" }
if ($env:REPLENISH_DEMO_USERNAME) { $parameters += "demoUsername=$($env:REPLENISH_DEMO_USERNAME)" }
if ($env:REPLENISH_DEMO_PASSWORD) { $parameters += "demoPassword=$($env:REPLENISH_DEMO_PASSWORD)" }
if ($env:REPLENISH_ADMIN_USERNAME) { $parameters += "adminUsername=$($env:REPLENISH_ADMIN_USERNAME)" }
if ($env:REPLENISH_ADMIN_PASSWORD) { $parameters += "adminPassword=$($env:REPLENISH_ADMIN_PASSWORD)" }

az group create --name $ResourceGroup --location $Location | Out-Host
az deployment group create `
  --resource-group $ResourceGroup `
  --name $DeploymentName `
  --template-file main.bicep `
  --parameters $parameters | Out-Host

$acrName = az deployment group show --resource-group $ResourceGroup --name $DeploymentName --query 'properties.outputs.AZURE_CONTAINER_REGISTRY_NAME.value' -o tsv
$acrLoginServer = az deployment group show --resource-group $ResourceGroup --name $DeploymentName --query 'properties.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT.value' -o tsv
$backendApp = az deployment group show --resource-group $ResourceGroup --name $DeploymentName --query 'properties.outputs.BACKEND_CONTAINER_APP_NAME.value' -o tsv
$frontendApp = az deployment group show --resource-group $ResourceGroup --name $DeploymentName --query 'properties.outputs.FRONTEND_CONTAINER_APP_NAME.value' -o tsv
$migrationJob = az deployment group show --resource-group $ResourceGroup --name $DeploymentName --query 'properties.outputs.MIGRATION_JOB_NAME.value' -o tsv

az acr build --registry $acrName --image "store-replenishment-backend:$ImageTag" --platform linux/amd64 --file '..\backend\Dockerfile' '..' | Out-Host
az acr build --registry $acrName --image "store-replenishment-frontend:$ImageTag" --platform linux/amd64 --file '..\frontend\Dockerfile' '..\frontend' | Out-Host

az containerapp registry set --resource-group $ResourceGroup --name $backendApp --server $acrLoginServer --identity system | Out-Host
az containerapp registry set --resource-group $ResourceGroup --name $frontendApp --server $acrLoginServer --identity system | Out-Host
az containerapp job registry set --resource-group $ResourceGroup --name $migrationJob --server $acrLoginServer --identity system | Out-Host

az containerapp job update --resource-group $ResourceGroup --name $migrationJob --image "$acrLoginServer/store-replenishment-backend:$ImageTag" | Out-Host
$executionName = az containerapp job start --resource-group $ResourceGroup --name $migrationJob --query name -o tsv
do {
    Start-Sleep -Seconds 5
    $migrationStatus = az containerapp job execution list `
      --resource-group $ResourceGroup `
      --name $migrationJob `
      --query "[?name=='$executionName'].properties.status | [0]" `
      -o tsv
} while ($migrationStatus -in @('Running', 'Processing', 'Pending'))
if ($migrationStatus -ne 'Succeeded') {
    throw "Database migration job $executionName finished with status '$migrationStatus'."
}

az containerapp update --resource-group $ResourceGroup --name $backendApp --image "$acrLoginServer/store-replenishment-backend:$ImageTag" | Out-Host
az containerapp update --resource-group $ResourceGroup --name $frontendApp --image "$acrLoginServer/store-replenishment-frontend:$ImageTag" | Out-Host

if (Get-Command azd -ErrorAction SilentlyContinue) {
    azd env set AZURE_RESOURCE_GROUP $ResourceGroup
    azd env set AZURE_LOCATION $Location
}
