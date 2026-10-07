param location string
param searchServiceName string
param searchSkuName string
param searchReplicaCount int
param searchPartitionCount int
param logAnalyticsWorkspaceId string
param tags object

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  sku: {
    name: searchSkuName
  }
  tags: tags
  properties: {
    replicaCount: searchReplicaCount
    partitionCount: searchPartitionCount
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'search-to-log-analytics'
  scope: search
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output searchServiceName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
