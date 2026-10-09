@description('Azure AI Search service name.')
param searchServiceName string

@description('Entra service principal object ID of the managed identity.')
param principalId string

@description('Query-only access, index document ingestion, or index definition management.')
@allowed([
  'reader'
  'indexContributor'
  'serviceContributor'
])
param accessLevel string

@description('ServicePrincipal for workload identities; User or Group for operators.')
@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param principalType string = 'ServicePrincipal'

resource searchService 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchServiceName
}

var roleIds = {
  reader: '1407120a-92aa-4202-b7e9-c0e197c71c8f' // Search Index Data Reader
  indexContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
  serviceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor
}
var roleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  roleIds[accessLevel]
)

resource searchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, principalId, roleDefinitionId)
  scope: searchService
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: principalType
  }
}

@description('Resource ID of the Search service-scoped role assignment.')
output roleAssignmentId string = searchRoleAssignment.id
