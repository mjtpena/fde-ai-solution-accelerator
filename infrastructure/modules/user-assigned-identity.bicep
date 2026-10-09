@description('Name of the user-assigned identity for one container app.')
param identityName string

@description('Azure region for the identity.')
param location string

@description('Resource tags.')
param tags object = {}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

@description('Resource ID to attach to the container app.')
output identityId string = identity.id

@description('Entra service principal object ID used as the RBAC principal.')
output principalId string = identity.properties.principalId

@description('Client ID used by Azure SDK credential selection.')
output clientId string = identity.properties.clientId
