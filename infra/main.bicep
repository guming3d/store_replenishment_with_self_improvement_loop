@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short project name used in resource names and tags.')
param projectName string = 'store-replenishment'

@description('Deployment environment name, for example dev, staging, or prod.')
param environmentName string = 'prod'

@description('Azure Container Registry name. Must be globally unique and alphanumeric.')
param acrName string = take(toLower(replace('cr${projectName}${environmentName}${uniqueString(resourceGroup().id)}', '-', '')), 50)

@description('Log Analytics workspace name.')
param logAnalyticsWorkspaceName string = '${projectName}-${environmentName}-law'

@description('Application Insights component name.')
param applicationInsightsName string = '${projectName}-${environmentName}-appi'

@description('Azure Container Apps managed environment name.')
param containerAppsEnvironmentName string = '${projectName}-${environmentName}-aca-env'

@description('Backend Container App name.')
param backendAppName string = '${projectName}-${environmentName}-backend'

@description('Frontend Container App name.')
param frontendAppName string = '${projectName}-${environmentName}-frontend'

@description('Database migration Container Apps Job name.')
param migrationJobName string = '${projectName}-${environmentName}-migrate'

@description('User-assigned identity shared by the backend and database migration job.')
param databaseIdentityName string = '${projectName}-${environmentName}-db-identity'

@description('Azure Database for PostgreSQL Flexible Server name.')
param postgresServerName string = take(toLower(replace('psql-${projectName}-${environmentName}-${uniqueString(resourceGroup().id)}', '_', '-')), 63)

@description('Application database name.')
param postgresDatabaseName string = 'replenishment'

@description('Initial backend container image. Scripts update this to the ACR image after build/push.')
param backendContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Initial frontend container image. Scripts update this to the ACR image after build/push.')
param frontendContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Backend application port.')
param backendTargetPort int = 8000

@description('Frontend application port.')
param frontendTargetPort int = 80

@description('Key Vault name for shared application configuration and future secret storage.')
param keyVaultName string = take(toLower(replace('kv${projectName}${environmentName}${uniqueString(resourceGroup().id)}', '-', '')), 24)

@description('Optional Foundry project endpoint for agent orchestration. Leave empty to keep agent mode disabled.')
param foundryProjectEndpoint string = ''

@description('Optional Foundry model deployment for agent orchestration. Leave empty to keep agent mode disabled.')
param foundryModelDeployment string = ''

@secure()
@description('Signing secret for the demo API bearer token. Override for shared environments.')
param authTokenSecret string = ''

@secure()
@description('Demo login username for deployed environments. Leave empty to disable cloud login until explicitly configured.')
param demoUsername string = ''

@secure()
@description('Demo login password for deployed environments. Leave empty to disable cloud login until explicitly configured.')
param demoPassword string = ''

@secure()
@description('Administrator login username. Leave empty to disable the admin console in this environment.')
param adminUsername string = ''

@secure()
@description('Administrator login password. Leave empty to disable the admin console in this environment.')
param adminPassword string = ''

var tags = {
  application: projectName
  environment: environmentName
  workload: 'store-replenishment'
}
var effectiveAuthTokenSecret = empty(authTokenSecret) ? uniqueString(subscription().subscriptionId, resourceGroup().id, backendAppName, 'replenishment-auth') : authTokenSecret
var demoCredentialSecrets = concat(
  empty(demoUsername) ? [] : [
    {
      name: 'demo-username'
      value: demoUsername
    }
  ],
  empty(demoPassword) ? [] : [
    {
      name: 'demo-password'
      value: demoPassword
    }
  ],
  empty(adminUsername) ? [] : [
    {
      name: 'admin-username'
      value: adminUsername
    }
  ],
  empty(adminPassword) ? [] : [
    {
      name: 'admin-password'
      value: adminPassword
    }
  ]
)
var demoCredentialEnv = concat(
  empty(demoUsername) ? [] : [
    {
      name: 'REPLENISH_DEMO_USERNAME'
      secretRef: 'demo-username'
    }
  ],
  empty(demoPassword) ? [] : [
    {
      name: 'REPLENISH_DEMO_PASSWORD'
      secretRef: 'demo-password'
    }
  ],
  empty(adminUsername) ? [] : [
    {
      name: 'REPLENISH_ADMIN_USERNAME'
      secretRef: 'admin-username'
    }
  ],
  empty(adminPassword) ? [] : [
    {
      name: 'REPLENISH_ADMIN_PASSWORD'
      secretRef: 'admin-password'
    }
  ]
)

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    enableRbacAuthorization: true
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    sku: {
      family: 'A'
      name: 'standard'
    }
    softDeleteRetentionInDays: 90
    tenantId: tenant().tenantId
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource databaseIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: databaseIdentityName
  location: location
  tags: tags
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresServerName
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: ''
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenant().tenantId
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    storage: {
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
  }
}

resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: postgresDatabaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource postgresAzureServicesFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

module postgresAdministrator 'postgres-admin.bicep' = {
  name: 'postgres-administrator'
  params: {
    serverName: postgres.name
    administratorObjectId: databaseIdentity.properties.principalId
    principalName: databaseIdentity.name
    tenantId: tenant().tenantId
  }
}

var postgresAsyncUrl = 'postgresql+asyncpg://${databaseIdentity.name}@${postgres.properties.fullyQualifiedDomainName}:5432/${postgresDatabaseName}?ssl=require'

resource backendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: backendAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'backend'
  })
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${databaseIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: concat([
        {
          name: 'auth-token-secret'
          value: effectiveAuthTokenSecret
        }
      ], demoCredentialSecrets)
      ingress: {
        external: false
        targetPort: backendTargetPort
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: backendContainerImage
          env: concat([
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'APPLICATIONINSIGHTS_ROLE_NAME'
              value: 'backend'
            }
            {
              name: 'FOUNDRY_PROJECT_ENDPOINT'
              value: foundryProjectEndpoint
            }
            {
              name: 'FOUNDRY_MODEL_DEPLOYMENT'
              value: foundryModelDeployment
            }
            {
              name: 'AZURE_USE_MANAGED_IDENTITY'
              value: 'true'
            }
            {
              name: 'ATTRIBUTION_POSTGRES_MANAGED_IDENTITY_CLIENT_ID'
              value: databaseIdentity.properties.clientId
            }
            {
              name: 'ATTRIBUTION_DATABASE_URL'
              value: postgresAsyncUrl
            }
            {
              name: 'ATTRIBUTION_POSTGRES_ENTRA_AUTH'
              value: 'true'
            }
            {
              name: 'ATTRIBUTION_RUN_MIGRATIONS_ON_STARTUP'
              value: 'true'
            }
            {
              name: 'ATTRIBUTION_WORKER_CONCURRENCY'
              value: '4'
            }
            {
              name: 'ATTRIBUTION_AUTO_PUBLISH'
              value: 'false'
            }
            {
              name: 'REPLENISH_AUTH_SECRET'
              secretRef: 'auth-token-secret'
            }
          ], demoCredentialEnv)
          probes: [
            {
              type: 'startup'
              httpGet: {
                path: '/api/health'
                port: backendTargetPort
              }
              initialDelaySeconds: 0
              periodSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'liveness'
              httpGet: {
                path: '/api/health'
                port: backendTargetPort
              }
              initialDelaySeconds: 10
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'readiness'
              httpGet: {
                path: '/api/health'
                port: backendTargetPort
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        // Two warm workers provide eight concurrent attribution slots so a run with
        // 20 modified SKUs can complete three p95 waves inside the 10-minute SLA.
        minReplicas: 2
        maxReplicas: 3
      }
    }
  }
}

resource migrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: migrationJobName
  location: location
  tags: union(tags, {
    'azd-service-name': 'migration'
  })
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${databaseIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvironment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 1
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'migration'
          image: backendContainerImage
          command: [
            '/bin/sh'
          ]
          args: [
            '-c'
            'alembic -c /app/backend/alembic.ini upgrade head'
          ]
          env: [
            {
              name: 'AZURE_USE_MANAGED_IDENTITY'
              value: 'true'
            }
            {
              name: 'ATTRIBUTION_POSTGRES_MANAGED_IDENTITY_CLIENT_ID'
              value: databaseIdentity.properties.clientId
            }
            {
              name: 'ATTRIBUTION_DATABASE_URL'
              value: postgresAsyncUrl
            }
            {
              name: 'ATTRIBUTION_POSTGRES_ENTRA_AUTH'
              value: 'true'
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
  dependsOn: [
    postgresAdministrator
    postgresDatabase
  ]
}

resource frontendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: frontendAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'frontend'
  })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
      ingress: {
        external: true
        targetPort: frontendTargetPort
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: frontendContainerImage
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'APPLICATIONINSIGHTS_ROLE_NAME'
              value: 'frontend'
            }
            {
              name: 'BACKEND_UPSTREAM'
              value: 'https://${backendApp.properties.configuration.ingress.fqdn}'
            }
          ]
          probes: [
            {
              type: 'startup'
              httpGet: {
                path: '/'
                port: frontendTargetPort
              }
              initialDelaySeconds: 0
              periodSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'liveness'
              httpGet: {
                path: '/'
                port: frontendTargetPort
              }
              initialDelaySeconds: 10
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'readiness'
              httpGet: {
                path: '/'
                port: frontendTargetPort
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

var acrPullRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource backendAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, backendApp.name, 'backend-acrpull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: backendApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource frontendAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, frontendApp.name, 'frontend-acrpull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: frontendApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource migrationAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, migrationJob.name, 'migration-acrpull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: migrationJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
output KEYVAULT_NAME string = keyVault.name
output KEYVAULT_URI string = keyVault.properties.vaultUri
output BACKEND_CONTAINER_APP_NAME string = backendApp.name
output FRONTEND_CONTAINER_APP_NAME string = frontendApp.name
output BACKEND_CONTAINER_APP_FQDN string = backendApp.properties.configuration.ingress.fqdn
output FRONTEND_CONTAINER_APP_FQDN string = frontendApp.properties.configuration.ingress.fqdn
output FRONTEND_CONTAINER_APP_URL string = 'https://${frontendApp.properties.configuration.ingress.fqdn}'
output MIGRATION_JOB_NAME string = migrationJob.name
output POSTGRES_SERVER_FQDN string = postgres.properties.fullyQualifiedDomainName
output POSTGRES_DATABASE_NAME string = postgresDatabase.name
output DATABASE_IDENTITY_CLIENT_ID string = databaseIdentity.properties.clientId
