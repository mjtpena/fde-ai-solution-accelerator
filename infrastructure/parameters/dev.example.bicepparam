using '../main.bicep'

param resourceGroupName = 'fde-dev-rg'
param location = 'eastus'
param resourcePrefix = 'fde-dev'
param tags = {
  environment: 'dev'
  workload: 'ai-solution-accelerator'
}

param logAnalyticsSkuName = 'PerGB2018'
param logAnalyticsRetentionInDays = 30
param storageSkuName = 'Standard_LRS'
param searchSkuName = 'basic'
param searchReplicaCount = 1
param searchPartitionCount = 1
param searchSemanticSearch = 'free'
param searchIndexName = 'chunks'
param searchVectorDimensions = 1536

param postgresSkuName = 'Standard_B1ms'
param postgresSkuTier = 'Burstable'
param postgresStorageSizeGb = 32
param postgresVersion = '16'
param postgresDatabaseName = 'accelerator'
param postgresAdministratorObjectId = readEnvironmentVariable('AZURE_POSTGRES_ADMIN_OBJECT_ID', '00000000-0000-0000-0000-000000000000')
param postgresAdministratorName = readEnvironmentVariable('AZURE_POSTGRES_ADMIN_NAME', 'replace-with-entra-admin')
param postgresAdministratorPrincipalType = readEnvironmentVariable('AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE', 'User')

param foundrySkuName = 'S0'
param modelDeploymentName = 'chat-model'
param modelName = readEnvironmentVariable('AZURE_MODEL_NAME', 'gpt-5-mini')
param modelVersion = readEnvironmentVariable('AZURE_MODEL_VERSION', '2025-08-07')
param modelFormat = 'OpenAI'
param modelSkuName = 'GlobalStandard'
param modelCapacity = 1
param embeddingDeploymentName = 'embedding-model'
param embeddingModelName = readEnvironmentVariable('AZURE_EMBEDDING_MODEL_NAME', 'text-embedding-3-small')
param embeddingModelVersion = readEnvironmentVariable('AZURE_EMBEDDING_MODEL_VERSION', '1')
param embeddingSkuName = 'GlobalStandard'
param embeddingCapacity = 1
param contentSafetySkuName = 'S0'
param deploymentPrincipalId = readEnvironmentVariable('AZURE_DEPLOYMENT_PRINCIPAL_ID', '')
// User when AZURE_DEPLOYMENT_PRINCIPAL_ID is a signed-in developer; ServicePrincipal for CI OIDC.
param deploymentPrincipalType = readEnvironmentVariable('AZURE_DEPLOYMENT_PRINCIPAL_TYPE', 'ServicePrincipal')
param evaluationPrincipalId = readEnvironmentVariable('AZURE_EVALUATION_PRINCIPAL_ID', '')
// The Foundry hosted agent's identity, known after its first deployment (see
// infrastructure/hosted_agent/README.md); deploy again with it set.
param hostedAgentPrincipalId = readEnvironmentVariable('AZURE_HOSTED_AGENT_PRINCIPAL_ID', '')

param keyVaultSkuName = 'standard'
param containerRegistrySkuName = 'Basic'

param networkAddressPrefix = '10.20.0.0/16'
param containerAppsSubnetPrefix = '10.20.0.0/23'
param postgresSubnetPrefix = '10.20.2.0/24'
param deployApplications = bool(readEnvironmentVariable('DEPLOY_APPLICATIONS', 'false'))
param apiImage = readEnvironmentVariable('API_IMAGE', '')
param webImage = readEnvironmentVariable('WEB_IMAGE', '')
param workerImage = readEnvironmentVariable('WORKER_IMAGE', '')
param apiEntraTenantId = readEnvironmentVariable('API_ENTRA_TENANT_ID', '')
param apiEntraAudience = readEnvironmentVariable('API_ENTRA_AUDIENCE', '')
