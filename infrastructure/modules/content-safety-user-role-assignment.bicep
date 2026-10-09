@description('Azure AI Content Safety account name.')
param contentSafetyAccountName string

@description('Entra service principal object ID of the caller.')
param principalId string

@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param principalType string = 'ServicePrincipal'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: contentSafetyAccountName
}

// Cognitive Services User: data-plane calls (text:shieldPrompt, text:analyze)
// with an Entra token. It grants no key listing and no management operations.
var cognitiveServicesUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'a97b65f3-24c7-4388-baec-2e87135dc908'
)

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, cognitiveServicesUserRoleDefinitionId)
  scope: account
  properties: {
    roleDefinitionId: cognitiveServicesUserRoleDefinitionId
    principalId: principalId
    principalType: principalType
  }
}

@description('Resource ID of the account-scoped role assignment.')
output roleAssignmentId string = roleAssignment.id
