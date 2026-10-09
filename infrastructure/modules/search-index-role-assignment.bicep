@description('Azure AI Search service name.')
param searchServiceName string

@description('Entra service principal object ID of the managed identity.')
param principalId string

@description('Choose query-only access or index document ingestion access.')
@allowed([
  'reader'
  'indexContributor'
])
param accessLevel string

resource searchService 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchServiceName
}

var roleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  accessLevel == 'reader'
    ? '1407120a-92aa-4202-b7e9-c0e197c71c8f'
    : '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
)

resource searchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, principalId, roleDefinitionId)
  scope: searchService
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Resource ID of the Search service-scoped role assignment.')
output roleAssignmentId string = searchRoleAssignment.id
