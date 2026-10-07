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

var foundryAgentConsumerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'
)

resource foundryRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryProject.id, principalId, foundryAgentConsumerRoleDefinitionId)
  scope: foundryProject
  properties: {
    roleDefinitionId: foundryAgentConsumerRoleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Resource ID of the Foundry project-scoped role assignment.')
output roleAssignmentId string = foundryRoleAssignment.id
