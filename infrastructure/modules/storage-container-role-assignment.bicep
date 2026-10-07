@description('Azure Storage account name.')
param storageAccountName string

@description('Blob container name to limit the assignment scope.')
param blobContainerName string

@description('Entra service principal object ID of the managed identity.')
param principalId string

@description('Choose read-only access or blob read/write access.')
@allowed([
  'reader'
  'contributor'
])
param accessLevel string

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: blobContainerName
}

var roleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  accessLevel == 'reader'
    ? '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
    : 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(blobContainer.id, principalId, roleDefinitionId)
  scope: blobContainer
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Resource ID of the blob-container-scoped role assignment.')
output roleAssignmentId string = storageRoleAssignment.id
