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

@description('Private blob container that receives uploaded source documents.')
param incomingContainerName string = 'incoming'

@description('Private blob container holding the authoritative ingested documents.')
param documentsContainerName string = 'documents'

@description('Storage queue of ingestion requests, and its poison queue.')
param queueName string = 'ingestion'
param poisonQueueName string = 'ingestion-poison'

@description('Azure AI Search SKU.')
param searchSkuName string

@description('Azure AI Search replica count.')
param searchReplicaCount int

@description('Azure AI Search partition count.')
param searchPartitionCount int

@description('Azure AI Search semantic ranker plan (the API gates on reranker scores).')
@allowed([
  'disabled'
  'free'
  'standard'
])
param searchSemanticSearch string = 'free'

@description('Search index the API queries and the worker writes.')
param searchIndexName string = 'chunks'

@description('Vector dimensions; must match the embedding deployment.')
param searchVectorDimensions int = 1536

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

@description('Embedding model deployment used for indexing and queries.')
param embeddingDeploymentName string = 'embedding-model'
param embeddingModelName string = 'text-embedding-3-small'
param embeddingModelVersion string = '1'
param embeddingSkuName string = 'GlobalStandard'
param embeddingCapacity int = 1

@description('Azure AI Content Safety SKU for prompt, document and answer screening.')
@allowed([
  'F0'
  'S0'
])
param contentSafetySkuName string

@description('Object ID of the deployment principal; granted Search Service Contributor to provision the index. Empty skips the grant.')
param deploymentPrincipalId string = ''

@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param deploymentPrincipalType string = 'ServicePrincipal'

@description('Object ID of the principal that runs the full evaluation (the VNet runner\'s OIDC identity); granted Search Index Data Reader, Foundry project access and Content Safety access. Empty skips the grants.')
param evaluationPrincipalId string = ''

@description('Azure Key Vault SKU.')
param keyVaultSkuName string

@description('Azure Container Registry SKU.')
param containerRegistrySkuName string

@description('Deploy real applications after their immutable images are published.')
param deployApplications bool = false
param apiImage string = ''
param webImage string = ''
param workerImage string = ''
param apiEntraTenantId string = ''
param apiEntraAudience string = ''
param networkAddressPrefix string
param containerAppsSubnetPrefix string
param postgresSubnetPrefix string

var suffix = uniqueString(subscription().id, resourceGroupName, location)
var sanitizedPrefix = replace(toLower(resourcePrefix), '-', '')
var storageAccountName = '${take(sanitizedPrefix, 11)}${take(suffix, 13)}'
var registryName = '${take(sanitizedPrefix, 37)}${take(suffix, 13)}'
var keyVaultName = '${take(sanitizedPrefix, 8)}-kv-${take(suffix, 8)}'
var searchServiceName = '${resourcePrefix}-search-${take(suffix, 6)}'
var postgresServerName = '${resourcePrefix}-postgres-${take(suffix, 6)}'
var foundryAccountName = '${take(sanitizedPrefix, 35)}${take(suffix, 13)}'
var foundryProjectName = '${resourcePrefix}-project'
var contentSafetyAccountName = '${take(sanitizedPrefix, 30)}safety${take(suffix, 13)}'
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
    incomingContainerName: incomingContainerName
    documentsContainerName: documentsContainerName
    queueName: queueName
    poisonQueueName: poisonQueueName
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
    semanticSearch: searchSemanticSearch
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
    delegatedSubnetId: network.outputs.postgresSubnetId
    privateDnsZoneId: network.outputs.postgresPrivateDnsZoneId
    administratorObjectId: postgresAdministratorObjectId
    administratorName: postgresAdministratorName
    administratorPrincipalType: postgresAdministratorPrincipalType
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module network './modules/network.bicep' = {
  name: 'network-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    networkName: '${resourcePrefix}-vnet-${take(suffix, 6)}'
    addressPrefix: networkAddressPrefix
    containerAppsSubnetPrefix: containerAppsSubnetPrefix
    postgresSubnetPrefix: postgresSubnetPrefix
    postgresPrivateDnsZoneName: '${postgresServerName}.private.postgres.database.azure.com'
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
    embeddingDeploymentName: embeddingDeploymentName
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    embeddingSkuName: embeddingSkuName
    embeddingCapacity: embeddingCapacity
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module contentSafety './modules/content-safety.bicep' = {
  name: 'content-safety-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    location: location
    accountName: contentSafetyAccountName
    skuName: contentSafetySkuName
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

module migratorIdentity './modules/user-assigned-identity.bicep' = {
  name: 'migrator-identity-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    identityName: '${resourcePrefix}-migrator-id-${take(suffix, 6)}'
    location: location
    tags: tags
  }
}

module migratorRegistryPull './modules/acr-pull-role-assignment.bicep' = {
  name: 'migrator-acr-pull-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: migratorIdentity.outputs.principalId
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

// The worker reads uploads, writes the authoritative copy, consumes the work queue
// and can only add to the poison queue. The API has no storage access.
module workerIncomingAccess './modules/storage-container-role-assignment.bicep' = {
  name: 'worker-incoming-reader-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    storageAccountName: storage.outputs.storageAccountName
    blobContainerName: incomingContainerName
    principalId: workerIdentity.outputs.principalId
    accessLevel: 'reader'
  }
}

module workerDocumentsAccess './modules/storage-container-role-assignment.bicep' = {
  name: 'worker-documents-contributor-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    storageAccountName: storage.outputs.storageAccountName
    blobContainerName: documentsContainerName
    principalId: workerIdentity.outputs.principalId
    accessLevel: 'contributor'
  }
}

module workerQueueAccess './modules/storage-queue-role-assignment.bicep' = {
  name: 'worker-queue-processor-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    storageAccountName: storage.outputs.storageAccountName
    queueName: queueName
    principalId: workerIdentity.outputs.principalId
    accessLevel: 'processor'
  }
}

module workerPoisonQueueAccess './modules/storage-queue-role-assignment.bicep' = {
  name: 'worker-poison-sender-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    storageAccountName: storage.outputs.storageAccountName
    queueName: poisonQueueName
    principalId: workerIdentity.outputs.principalId
    accessLevel: 'sender'
  }
}

module workerFoundryAccess './modules/foundry-user-role-assignment.bicep' = {
  name: 'worker-foundry-user-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    foundryAccountName: foundry.outputs.accountName
    foundryProjectName: foundry.outputs.projectName
    principalId: workerIdentity.outputs.principalId
  }
}

module apiTelemetryAccess './modules/monitoring-publisher-role-assignment.bicep' = {
  name: 'api-telemetry-publisher-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    applicationInsightsName: monitoring.outputs.applicationInsightsName
    principalId: apiIdentity.outputs.principalId
  }
}

module migratorTelemetryAccess './modules/monitoring-publisher-role-assignment.bicep' = {
  name: 'migrator-telemetry-publisher-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    applicationInsightsName: monitoring.outputs.applicationInsightsName
    principalId: migratorIdentity.outputs.principalId
  }
}

module evaluationSearchAccess './modules/search-index-role-assignment.bicep' = if (!empty(evaluationPrincipalId)) {
  name: 'evaluation-search-reader-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    searchServiceName: search.outputs.searchServiceName
    principalId: evaluationPrincipalId
    accessLevel: 'reader'
  }
}

module evaluationFoundryAccess './modules/foundry-user-role-assignment.bicep' = if (!empty(evaluationPrincipalId)) {
  name: 'evaluation-foundry-user-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    foundryAccountName: foundry.outputs.accountName
    foundryProjectName: foundry.outputs.projectName
    principalId: evaluationPrincipalId
  }
}

module evaluationContentSafetyAccess './modules/content-safety-user-role-assignment.bicep' = if (!empty(evaluationPrincipalId)) {
  name: 'evaluation-content-safety-user-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    contentSafetyAccountName: contentSafety.outputs.accountName
    principalId: evaluationPrincipalId
  }
}

module deployerSearchAccess './modules/search-index-role-assignment.bicep' = if (!empty(deploymentPrincipalId)) {
  name: 'deployer-search-service-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    searchServiceName: search.outputs.searchServiceName
    principalId: deploymentPrincipalId
    principalType: deploymentPrincipalType
    accessLevel: 'serviceContributor'
  }
}

module apiFoundryAccess './modules/foundry-user-role-assignment.bicep' = {
  name: 'api-foundry-user-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    foundryAccountName: foundry.outputs.accountName
    foundryProjectName: foundry.outputs.projectName
    principalId: apiIdentity.outputs.principalId
  }
}

// The API screens every chat turn (prompt, retrieved chunks, answer) and fails
// closed without this grant.
module apiContentSafetyAccess './modules/content-safety-user-role-assignment.bicep' = {
  name: 'api-content-safety-user-${take(suffix, 8)}'
  scope: environmentResourceGroup
  params: {
    contentSafetyAccountName: contentSafety.outputs.accountName
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
    migrationJobName: '${resourcePrefix}-migrate-${take(suffix, 6)}'
    infrastructureSubnetId: network.outputs.containerAppsSubnetId
    deployApplications: deployApplications
    apiImage: apiImage
    webImage: webImage
    workerImage: workerImage
    registryLoginServer: containerRegistry.outputs.loginServer
    apiIdentityId: apiIdentity.outputs.identityId
    apiIdentityClientId: apiIdentity.outputs.clientId
    webIdentityId: webIdentity.outputs.identityId
    webIdentityClientId: webIdentity.outputs.clientId
    workerIdentityId: workerIdentity.outputs.identityId
    workerIdentityClientId: workerIdentity.outputs.clientId
    migratorIdentityId: migratorIdentity.outputs.identityId
    migratorIdentityClientId: migratorIdentity.outputs.clientId
    apiEntraTenantId: apiEntraTenantId
    apiEntraAudience: apiEntraAudience
    postgresHost: postgres.outputs.fullyQualifiedDomainName
    postgresDatabaseName: postgresDatabaseName
    postgresAdministratorName: postgresAdministratorName
    blobEndpoint: storage.outputs.blobEndpoint
    queueEndpoint: storage.outputs.queueEndpoint
    incomingContainerName: incomingContainerName
    documentsContainerName: documentsContainerName
    queueName: queueName
    poisonQueueName: poisonQueueName
    searchEndpoint: search.outputs.searchEndpoint
    searchIndexName: searchIndexName
    searchVectorDimensions: searchVectorDimensions
    projectEndpoint: foundry.outputs.projectEndpoint
    modelDeploymentName: foundry.outputs.modelDeploymentName
    embeddingDeploymentName: foundry.outputs.embeddingDeploymentName
    contentSafetyEndpoint: contentSafety.outputs.endpoint
    applicationInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
    tags: tags
  }
  dependsOn: [
    apiRegistryPull
    webRegistryPull
    workerRegistryPull
    migratorRegistryPull
    apiContentSafetyAccess
  ]
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
output contentSafetyAccountName string = contentSafety.outputs.accountName
output contentSafetyEndpoint string = contentSafety.outputs.endpoint
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
output virtualNetworkId string = network.outputs.networkId
output apiInternalUrl string = containerApps.outputs.apiInternalUrl
output webUrl string = containerApps.outputs.webUrl
output queueEndpoint string = storage.outputs.queueEndpoint
output incomingContainerName string = incomingContainerName
output queueName string = queueName
output searchIndexName string = searchIndexName
output searchVectorDimensions int = searchVectorDimensions
output embeddingDeploymentName string = foundry.outputs.embeddingDeploymentName
output migratorIdentityPrincipalId string = migratorIdentity.outputs.principalId
output migratorIdentityClientId string = migratorIdentity.outputs.clientId
output migratorDatabaseRoleName string = 'accelerator_migrator'
output apiDatabaseRoleName string = 'accelerator_api'
output workerDatabaseRoleName string = 'accelerator_worker'
