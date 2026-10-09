@description('Azure region for the Content Safety account.')
param location string

@description('Azure AI Content Safety account name; also its custom subdomain.')
param accountName string

@description('Content Safety SKU (S0 for production traffic, F0 for a free trial account).')
@allowed([
  'F0'
  'S0'
])
param skuName string

param logAnalyticsWorkspaceId string
param tags object

// Entra-only: key authentication is disabled, so callers need a token for
// https://cognitiveservices.azure.com/.default and a data-plane role. A custom
// subdomain is required for Entra token authentication. Public network access
// matches the other Azure AI services in this deployment (ADR-0006).
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'ContentSafety'
  sku: {
    name: skuName
  }
  tags: tags
  properties: {
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'content-safety-to-log-analytics'
  scope: account
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

output accountName string = account.name
output endpoint string = account.properties.endpoint
