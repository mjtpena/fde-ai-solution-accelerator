param location string
param workspaceName string
param appInsightsName string
param logAnalyticsSkuName string
param logAnalyticsRetentionInDays int
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: logAnalyticsSkuName
    }
    retentionInDays: logAnalyticsRetentionInDays
    features: {
      disableLocalAuth: true
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

output workspaceName string = workspace.name
output workspaceId string = workspace.id
output workspaceCustomerId string = workspace.properties.customerId
output applicationInsightsId string = applicationInsights.id
