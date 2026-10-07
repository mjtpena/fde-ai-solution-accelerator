param location string
param environmentName string
param logAnalyticsWorkspaceId string
param apiAppName string
param webAppName string
param workerAppName string
param tags object

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'container-apps-to-log-analytics'
  scope: environment
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

output environmentName string = environment.name
output environmentId string = environment.id
output appNames object = {
  api: apiAppName
  web: webAppName
  worker: workerAppName
}
