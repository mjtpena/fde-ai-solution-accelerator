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
param blobContainerName = 'documents'
param searchSkuName = 'basic'
param searchReplicaCount = 1
param searchPartitionCount = 1

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
param modelName = 'gpt-4o-mini'
param modelVersion = '2024-07-18'
param modelFormat = 'OpenAI'
param modelSkuName = 'GlobalStandard'
param modelCapacity = 1

param keyVaultSkuName = 'standard'
param containerRegistrySkuName = 'Basic'
