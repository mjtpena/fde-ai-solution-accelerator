@description('Azure Storage account name.')
param storageAccountName string

@description('Queue name to limit the assignment scope.')
param queueName string

@description('Entra service principal object ID of the managed identity.')
param principalId string

@description('Receive and delete messages (processor) or only add them (sender).')
@allowed([
  'processor'
  'sender'
])
param accessLevel string

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource queue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' existing = {
  parent: queueService
  name: queueName
}

var roleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  accessLevel == 'processor'
    ? '8a0f0c08-91a1-4084-bc3d-661d67233fed'
    : 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'
)

resource queueRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, principalId, roleDefinitionId)
  scope: queue
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Resource ID of the queue-scoped role assignment.')
output roleAssignmentId string = queueRoleAssignment.id
