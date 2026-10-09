param location string
param environmentName string
param logAnalyticsWorkspaceId string
param apiAppName string
param webAppName string
param workerAppName string
param tags object
param infrastructureSubnetId string
param deployApplications bool
param apiImage string
param webImage string
param workerImage string
param registryLoginServer string
param apiIdentityId string
param apiIdentityClientId string
param webIdentityId string
param webIdentityClientId string
param workerIdentityId string
param workerIdentityClientId string
param apiEntraTenantId string
param apiEntraAudience string
param postgresHost string
param postgresDatabaseName string
param blobEndpoint string
param searchEndpoint string
param projectEndpoint string

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      internal: false
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

var workloads = [
  {
    name: apiAppName
    image: apiImage
    identityId: apiIdentityId
    clientId: apiIdentityClientId
    port: 8000
    external: true
    env: [
      { name: 'API_ENVIRONMENT', value: 'development' }
      { name: 'API_ENTRA_TENANT_ID', value: apiEntraTenantId }
      { name: 'API_ENTRA_AUDIENCE', value: apiEntraAudience }
      { name: 'PGUSER', value: 'accelerator_api' }
      { name: 'AZURE_AI_PROJECT_ENDPOINT', value: projectEndpoint }
    ]
  }
  {
    name: webAppName
    image: webImage
    identityId: webIdentityId
    clientId: webIdentityClientId
    port: 3000
    external: true
    env: []
  }
  {
    name: workerAppName
    image: workerImage
    identityId: workerIdentityId
    clientId: workerIdentityClientId
    port: 0
    external: false
    env: [
      { name: 'PGUSER', value: 'accelerator_worker' }
    ]
  }
]

resource apps 'Microsoft.App/containerApps@2024-03-01' = [for workload in workloads: if (deployApplications) {
  name: workload.name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${workload.identityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: workload.port == 0 ? null : {
        external: workload.external
        targetPort: workload.port
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryLoginServer
          identity: workload.identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: workload.image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat([
            { name: 'AZURE_CLIENT_ID', value: workload.clientId }
          ], workload.port == 3000 ? [] : [
            { name: 'PGHOST', value: postgresHost }
            { name: 'PGDATABASE', value: postgresDatabaseName }
            { name: 'PGSSLMODE', value: 'verify-full' }
            { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: blobEndpoint }
            { name: 'AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
          ], workload.env, workload.name == apiAppName ? [
            { name: 'API_WEB_ORIGIN', value: 'https://${webAppName}.${environment.properties.defaultDomain}' }
          ] : [])
        }
      ]
      scale: {
        minReplicas: workload.port == 0 ? 1 : 0
        maxReplicas: 1
      }
    }
  }
}]

resource appDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = [for (workload, index) in workloads: if (deployApplications) {
  name: 'app-to-log-analytics'
  scope: apps[index]
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}]

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
output apiUrl string = deployApplications ? 'https://${apps[0]!.properties.configuration.ingress!.fqdn}' : ''
output webUrl string = deployApplications ? 'https://${apps[1]!.properties.configuration.ingress!.fqdn}' : ''
