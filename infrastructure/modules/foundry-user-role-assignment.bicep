@description('Microsoft Foundry account name.')
param foundryAccountName string

@description('Microsoft Foundry project name.')
param foundryProjectName string

@description('Entra service principal object ID of the managed identity.')
param principalId string

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundryAccount
  name: foundryProjectName
}

// Foundry User (formerly Azure AI User): the least-privilege built-in role that
// includes direct model inference (chat and embeddings) on the project. Foundry
// Agent Consumer (eed3b665-...) only covers published agent endpoints, so the
// FoundryChatClient and FoundryEmbeddingClient calls would be denied under it.
var foundryUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '53ca6127-db72-4b80-b1b0-d745d6d5456d'
)

resource foundryRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryProject.id, principalId, foundryUserRoleDefinitionId)
  scope: foundryProject
  properties: {
    roleDefinitionId: foundryUserRoleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Resource ID of the Foundry project-scoped role assignment.')
output roleAssignmentId string = foundryRoleAssignment.id
