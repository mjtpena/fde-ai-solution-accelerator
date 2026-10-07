targetScope = 'subscription'

@description('Name of the resource group to create for this environment.')
param resourceGroupName string

@description('Azure region for the resource group and regional resources.')
param location string

@description('Short lowercase prefix used to derive resource names.')
@minLength(2)
@maxLength(20)
param resourcePrefix string

@description('Tags applied to provisioned resources.')
param tags object = {}

@description('Log Analytics workspace SKU.')
param logAnalyticsSkuName string

@description('Log Analytics retention in days.')
param logAnalyticsRetentionInDays int

@description('Azure Storage account SKU.')
param storageSkuName string

@description('Private blob container for source documents.')
param blobContainerName string

@description('Azure AI Search SKU.')
param searchSkuName string

@description('Azure AI Search replica count.')
param searchReplicaCount int

@description('Azure AI Search partition count.')
param searchPartitionCount int

@description('PostgreSQL Flexible Server SKU name.')
param postgresSkuName string

@description('PostgreSQL Flexible Server SKU tier.')
param postgresSkuTier string

@description('PostgreSQL Flexible Server storage size in GB.')
param postgresStorageSizeGb int

@description('PostgreSQL Flexible Server major version.')
param postgresVersion string

@description('PostgreSQL database name.')
param postgresDatabaseName string

@description('Entra principal object ID for the PostgreSQL administrator.')
param postgresAdministratorObjectId string

@description('Entra principal name for the PostgreSQL administrator.')
param postgresAdministratorName string

@description('Entra principal type for the PostgreSQL administrator.')
@allowed([
  'User'
  'ServicePrincipal'
])
param postgresAdministratorPrincipalType string

@description('Azure AI Foundry account SKU.')
param foundrySkuName string

@description('Azure AI Foundry project model deployment name.')
param modelDeploymentName string

@description('Model name to deploy in Azure AI Foundry.')
param modelName string

@description('Model version to deploy.')
param modelVersion string

@description('Model format, for example OpenAI.')
param modelFormat string

@description('Model deployment SKU.')
param modelSkuName string

@description('Model deployment capacity.')
param modelCapacity int

@description('Azure Key Vault SKU.')
param keyVaultSkuName string

@description('Azure Container Registry SKU.')
param containerRegistrySkuName string

var suffix = uniqueString(subscription().id, resourceGroupName, location)
var sanitizedPrefix = replace(toLower(resourcePrefix), '-', '')
var storageAccountName = '${take(sanitizedPrefix, 11)}${take(suffix, 13)}'
var registryName = '${take(sanitizedPrefix, 37)}${take(suffix, 13)}'
var keyVaultName = '${take(sanitizedPrefix, 8)}-kv-${take(suffix, 8)}'
var searchServiceName = '${resourcePrefix}-search-${take(suffix, 6)}'
var postgresServerName = '${resourcePrefix}-postgres-${take(suffix, 6)}'
var foundryAccountName = '${take(sanitizedPrefix, 35)}${take(suffix, 13)}'
var foundryProjectName = '${resourcePrefix}-project'
var workspaceName = '${resourcePrefix}-logs-${take(suffix, 6)}'
var appInsightsName = '${resourcePrefix}-appi-${take(suffix, 6)}'
var containerAppsEnvironmentName = '${resourcePrefix}-apps-${take(suffix, 6)}'

resource environmentResourceGroup 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module monitoring './modules/monitoring.bicep' = {
  name: 'monitoring-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    workspaceName: workspaceName
    appInsightsName: appInsightsName
    logAnalyticsSkuName: logAnalyticsSkuName
    logAnalyticsRetentionInDays: logAnalyticsRetentionInDays
    tags: tags
  }
}

module storage './modules/storage.bicep' = {
  name: 'storage-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    storageAccountName: storageAccountName
    storageSkuName: storageSkuName
    blobContainerName: blobContainerName
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module search './modules/search.bicep' = {
  name: 'search-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    searchServiceName: searchServiceName
    searchSkuName: searchSkuName
    searchReplicaCount: searchReplicaCount
    searchPartitionCount: searchPartitionCount
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module postgres './modules/postgres.bicep' = {
  name: 'postgres-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    serverName: postgresServerName
    skuName: postgresSkuName
    skuTier: postgresSkuTier
    storageSizeGb: postgresStorageSizeGb
    version: postgresVersion
    databaseName: postgresDatabaseName
    administratorObjectId: postgresAdministratorObjectId
    administratorName: postgresAdministratorName
    administratorPrincipalType: postgresAdministratorPrincipalType
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module foundry './modules/foundry.bicep' = {
  name: 'foundry-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    accountName: foundryAccountName
    projectName: foundryProjectName
    accountSkuName: foundrySkuName
    modelDeploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    modelFormat: modelFormat
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module keyVault './modules/key-vault.bicep' = {
  name: 'key-vault-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    vaultName: keyVaultName
    skuName: keyVaultSkuName
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module containerRegistry './modules/container-registry.bicep' = {
  name: 'container-registry-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    registryName: registryName
    location: location
    skuName: containerRegistrySkuName
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module apiIdentity './modules/user-assigned-identity.bicep' = {
  name: 'api-identity-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    identityName: '${resourcePrefix}-api-id-${take(suffix, 6)}'
    location: location
    tags: tags
  }
}

module webIdentity './modules/user-assigned-identity.bicep' = {
  name: 'web-identity-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    identityName: '${resourcePrefix}-web-id-${take(suffix, 6)}'
    location: location
    tags: tags
  }
}

module workerIdentity './modules/user-assigned-identity.bicep' = {
  name: 'worker-identity-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    identityName: '${resourcePrefix}-worker-id-${take(suffix, 6)}'
    location: location
    tags: tags
  }
}

module apiRegistryPull './modules/acr-pull-role-assignment.bicep' = {
  name: 'api-acr-pull-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: apiIdentity.outputs.principalId
  }
}

module webRegistryPull './modules/acr-pull-role-assignment.bicep' = {
  name: 'web-acr-pull-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: webIdentity.outputs.principalId
  }
}

module workerRegistryPull './modules/acr-pull-role-assignment.bicep' = {
  name: 'worker-acr-pull-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: workerIdentity.outputs.principalId
  }
}

module foundryRegistryPull './modules/acr-pull-role-assignment.bicep' = {
  name: 'foundry-project-acr-pull-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: foundry.outputs.projectPrincipalId
  }
}

module apiSearchAccess './modules/search-index-role-assignment.bicep' = {
  name: 'api-search-reader-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    searchServiceName: search.outputs.searchServiceName
    principalId: apiIdentity.outputs.principalId
    accessLevel: 'reader'
  }
}

module workerSearchAccess './modules/search-index-role-assignment.bicep' = {
  name: 'worker-search-contributor-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    searchServiceName: search.outputs.searchServiceName
    principalId: workerIdentity.outputs.principalId
    accessLevel: 'indexContributor'
  }
}

module apiBlobAccess './modules/storage-container-role-assignment.bicep' = {
  name: 'api-blob-contributor-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    storageAccountName: storage.outputs.storageAccountName
    blobContainerName: blobContainerName
    principalId: apiIdentity.outputs.principalId
    accessLevel: 'contributor'
  }
}

module workerBlobAccess './modules/storage-container-role-assignment.bicep' = {
  name: 'worker-blob-reader-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    storageAccountName: storage.outputs.storageAccountName
    blobContainerName: blobContainerName
    principalId: workerIdentity.outputs.principalId
    accessLevel: 'reader'
  }
}

module apiFoundryAccess './modules/foundry-agent-consumer-role-assignment.bicep' = {
  name: 'api-foundry-consumer-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    foundryAccountName: foundry.outputs.accountName
    foundryProjectName: foundry.outputs.projectName
    principalId: apiIdentity.outputs.principalId
  }
}

module containerApps './modules/container-apps.bicep' = {
  name: 'container-apps-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    environmentName: containerAppsEnvironmentName
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    apiAppName: '${resourcePrefix}-api-${take(suffix, 6)}'
    webAppName: '${resourcePrefix}-web-${take(suffix, 6)}'
    workerAppName: '${resourcePrefix}-worker-${take(suffix, 6)}'
    tags: tags
  }
}

output resourceGroupName string = environmentResourceGroup.name
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceId
output applicationInsightsId string = monitoring.outputs.applicationInsightsId
output storageAccountName string = storage.outputs.storageAccountName
output blobEndpoint string = storage.outputs.blobEndpoint
output searchServiceName string = search.outputs.searchServiceName
output searchEndpoint string = search.outputs.searchEndpoint
output postgresServerName string = postgres.outputs.serverName
output postgresFqdn string = postgres.outputs.fullyQualifiedDomainName
output postgresDatabaseName string = postgres.outputs.databaseName
output foundryAccountName string = foundry.outputs.accountName
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectId string = foundry.outputs.projectId
output projectEndpoint string = foundry.outputs.projectEndpoint
output projectPrincipalId string = foundry.outputs.projectPrincipalId
output modelDeploymentName string = foundry.outputs.modelDeploymentName
output keyVaultName string = keyVault.outputs.vaultName
output keyVaultUri string = keyVault.outputs.vaultUri
output registryName string = containerRegistry.outputs.registryName
output registryLoginServer string = containerRegistry.outputs.loginServer
output registryId string = containerRegistry.outputs.registryId
output containerAppsEnvironmentName string = containerApps.outputs.environmentName
output containerAppsEnvironmentId string = containerApps.outputs.environmentId
output containerAppNames object = containerApps.outputs.appNames
output apiIdentityId string = apiIdentity.outputs.identityId
output apiIdentityPrincipalId string = apiIdentity.outputs.principalId
output apiIdentityClientId string = apiIdentity.outputs.clientId
output webIdentityId string = webIdentity.outputs.identityId
output webIdentityPrincipalId string = webIdentity.outputs.principalId
output webIdentityClientId string = webIdentity.outputs.clientId
output workerIdentityId string = workerIdentity.outputs.identityId
output workerIdentityPrincipalId string = workerIdentity.outputs.principalId
output workerIdentityClientId string = workerIdentity.outputs.clientId
