@description('Application Insights component name.')
param applicationInsightsName string

@description('Entra service principal object ID of the managed identity.')
param principalId string

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

// Monitoring Metrics Publisher: send telemetry with Entra auth (local auth is off).
var roleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4b42-8a1d-c0b25e3be3c6'
)

resource publisherAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(applicationInsights.id, principalId, roleDefinitionId)
  scope: applicationInsights
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Resource ID of the Application Insights-scoped role assignment.')
output roleAssignmentId string = publisherAssignment.id
