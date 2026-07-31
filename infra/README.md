# Store Replenishment Azure Infrastructure

Infrastructure for the FastAPI backend, React frontend, and durable attribution worker. Deployment provisions shared observability, PostgreSQL persistence, managed identities, a migration job, and Azure Container Apps.

## Files

- `main.bicep` / `main.parameters.json` - ACR, Log Analytics, Application Insights, PostgreSQL Flexible Server, shared database managed identity, Container Apps migration job, backend Container App on port 8000, and frontend Container App on port 80.
- `postgres-admin.bicep` - nested deployment that assigns the runtime-created user-assigned identity as the PostgreSQL Entra administrator.
- `azure.yaml` - azd services for `backend` and `frontend`.
- `deploy.sh` / `deploy.ps1` - reference build, push, and deploy scripts.

## Reference deploy steps

```bash
cd infra
az login
az group create --name rg-store-replenishment --location eastus
az deployment group create \
  --resource-group rg-store-replenishment \
  --template-file main.bicep \
  --parameters @main.parameters.json
```

Build and push images with ACR cloud build:

```bash
ACR_NAME=crstorereplenishmentprod
TAG=$(date +%Y%m%d%H%M%S)
az acr build --registry $ACR_NAME --image store-replenishment-backend:$TAG --platform linux/amd64 --file ../backend/Dockerfile ..
az acr build --registry $ACR_NAME --image store-replenishment-frontend:$TAG --platform linux/amd64 --file ../frontend/Dockerfile ../frontend
```

The reference scripts bind Container Apps and the migration job to ACR, update the migration image, run Alembic to completion, and only then roll out backend/frontend images. PostgreSQL password authentication is disabled; the backend and migration job use the shared user-assigned identity through Entra authentication. As a safety net for direct `azd deploy`, each backend revision also runs `alembic upgrade head` behind a PostgreSQL advisory lock before accepting traffic. Foundry calls use the backend system identity unless `FOUNDRY_MANAGED_IDENTITY_CLIENT_ID` explicitly selects a separate user-assigned identity.

```bash
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
az containerapp registry set --resource-group rg-store-replenishment --name store-replenishment-backend --server $ACR_LOGIN_SERVER --identity system
az containerapp registry set --resource-group rg-store-replenishment --name store-replenishment-frontend --server $ACR_LOGIN_SERVER --identity system
az containerapp job registry set --resource-group rg-store-replenishment --name store-replenishment-prod-migrate --server $ACR_LOGIN_SERVER --identity system
az containerapp job update --resource-group rg-store-replenishment --name store-replenishment-prod-migrate --image $ACR_LOGIN_SERVER/store-replenishment-backend:$TAG
az containerapp job start --resource-group rg-store-replenishment --name store-replenishment-prod-migrate
az containerapp update --resource-group rg-store-replenishment --name store-replenishment-backend --image $ACR_LOGIN_SERVER/store-replenishment-backend:$TAG
az containerapp update --resource-group rg-store-replenishment --name store-replenishment-frontend --image $ACR_LOGIN_SERVER/store-replenishment-frontend:$TAG
```

Or use the reference scripts:

```bash
cd infra
./deploy.sh
# or on Windows
./deploy.ps1
```
