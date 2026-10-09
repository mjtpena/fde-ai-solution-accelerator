param location string
param environmentName string
param logAnalyticsWorkspaceId string
param apiAppName string
param webAppName string
param workerAppName string
param migrationJobName string
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
param migratorIdentityId string
param migratorIdentityClientId string
param apiEntraTenantId string
param apiEntraAudience string
param postgresHost string
param postgresDatabaseName string
param blobEndpoint string
param queueEndpoint string
param incomingContainerName string
param documentsContainerName string
param queueName string
param poisonQueueName string
param searchEndpoint string
param searchIndexName string
param searchVectorDimensions int
param projectEndpoint string
param modelDeploymentName string
param embeddingDeploymentName string
param applicationInsightsConnectionString string

@description('Minimum API replicas; 1 avoids cold starts on the first chat request.')
param apiMinReplicas int = 1

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

var defaultDomain = environment.properties.defaultDomain
var webOrigin = 'https://${webAppName}.${defaultDomain}'
// Internal ingress: only apps inside the environment (the web server) reach the API.
var apiInternalUrl = 'https://${apiAppName}.internal.${defaultDomain}'

// Database roles are bound to each identity's object ID by bootstrap-postgres.ps1.
// The password is an Entra token fetched per connection; TLS is always verified.
var apiDatabaseUrl = 'postgresql+asyncpg://accelerator_api@${postgresHost}:5432/${postgresDatabaseName}'
var workerDatabaseUrl = 'postgresql://accelerator_worker@${postgresHost}:5432/${postgresDatabaseName}'
var migratorDatabaseUrl = 'postgresql+asyncpg://accelerator_migrator@${postgresHost}:5432/${postgresDatabaseName}'

// Settings that depend on the environment's runtime domain are added inside the
// resource loops; the loop collections themselves must be known at deployment start.
var apiSettings = [
  { name: 'API_ENVIRONMENT', value: 'production' }
  { name: 'API_ENTRA_TENANT_ID', value: apiEntraTenantId }
  { name: 'API_ENTRA_AUDIENCE', value: apiEntraAudience }
  { name: 'API_DATABASE_AUTH_MODE', value: 'managed_identity' }
  { name: 'API_FOUNDRY_PROJECT_ENDPOINT', value: projectEndpoint }
  { name: 'API_FOUNDRY_MODEL_DEPLOYMENT', value: modelDeploymentName }
  { name: 'API_FOUNDRY_EMBEDDING_DEPLOYMENT', value: embeddingDeploymentName }
  { name: 'API_SEARCH_ENDPOINT', value: searchEndpoint }
  { name: 'API_SEARCH_INDEX_NAME', value: searchIndexName }
  { name: 'API_SEARCH_VECTOR_DIMENSIONS', value: string(searchVectorDimensions) }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: applicationInsightsConnectionString }
]

var workloads = [
  {
    name: apiAppName
    image: apiImage
    identityId: apiIdentityId
    port: 8000
    external: false
    minReplicas: apiMinReplicas
    probePath: '/healthz'
    readinessPath: '/readyz'
    kind: 'api'
    env: concat(apiSettings, [
      { name: 'AZURE_CLIENT_ID', value: apiIdentityClientId }
      { name: 'API_DATABASE_URL', value: apiDatabaseUrl }
    ])
  }
  {
    name: webAppName
    image: webImage
    identityId: webIdentityId
    port: 3000
    external: true
    minReplicas: 0
    // Liveness must not depend on the API; readiness does (through the internal hop).
    probePath: '/'
    readinessPath: '/api/health'
    kind: 'web'
    env: [
      { name: 'AZURE_CLIENT_ID', value: webIdentityClientId }
    ]
  }
  {
    name: workerAppName
    image: workerImage
    identityId: workerIdentityId
    port: 0
    external: false
    minReplicas: 1
    probePath: ''
    readinessPath: ''
    kind: 'worker'
    env: [
      { name: 'AZURE_CLIENT_ID', value: workerIdentityClientId }
      { name: 'INGESTION_ENVIRONMENT', value: 'production' }
      { name: 'INGESTION_MANAGED_IDENTITY_CLIENT_ID', value: workerIdentityClientId }
      { name: 'INGESTION_BLOB_ACCOUNT_URL', value: blobEndpoint }
      { name: 'INGESTION_QUEUE_ACCOUNT_URL', value: queueEndpoint }
      { name: 'INGESTION_INCOMING_CONTAINER', value: incomingContainerName }
      { name: 'INGESTION_DOCUMENTS_CONTAINER', value: documentsContainerName }
      { name: 'INGESTION_QUEUE_NAME', value: queueName }
      { name: 'INGESTION_POISON_QUEUE_NAME', value: poisonQueueName }
      { name: 'INGESTION_DATABASE_URL', value: workerDatabaseUrl }
      { name: 'INGESTION_DATABASE_AUTH_MODE', value: 'managed_identity' }
      { name: 'INGESTION_SEARCH_ENDPOINT', value: searchEndpoint }
      { name: 'INGESTION_SEARCH_INDEX_NAME', value: searchIndexName }
      { name: 'INGESTION_VECTOR_DIMENSIONS', value: string(searchVectorDimensions) }
      { name: 'INGESTION_FOUNDRY_PROJECT_ENDPOINT', value: projectEndpoint }
      { name: 'INGESTION_FOUNDRY_EMBEDDING_DEPLOYMENT', value: embeddingDeploymentName }
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
          env: concat(
            workload.env,
            workload.kind == 'api' ? [{ name: 'API_WEB_ORIGIN', value: webOrigin }] : [],
            workload.kind == 'web' ? [{ name: 'API_BASE_URL', value: apiInternalUrl }] : []
          )
          probes: workload.port == 0 ? [] : [
            {
              type: 'Liveness'
              httpGet: {
                path: workload.probePath
                port: workload.port
              }
              periodSeconds: 15
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: workload.readinessPath
                port: workload.port
              }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: workload.minReplicas
        maxReplicas: workload.port == 0 ? 1 : 3
      }
    }
  }
}]

// Schema migrations run once per deployment, before the new revision serves, under
// a separate identity whose database role alone may change the schema.
resource migrationJob 'Microsoft.App/jobs@2024-03-01' = if (deployApplications) {
  name: migrationJobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${migratorIdentityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registryLoginServer
          identity: migratorIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: apiImage
          command: [
            'python'
            '-m'
            'accelerator.migrations'
            'upgrade'
            'head'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          // Only what MigrationSettings reads; the owner then grants each runtime
          // role exactly its table privileges.
          env: [
            { name: 'AZURE_CLIENT_ID', value: migratorIdentityClientId }
            { name: 'API_DATABASE_URL', value: migratorDatabaseUrl }
            { name: 'API_DATABASE_AUTH_MODE', value: 'managed_identity' }
            { name: 'API_DATABASE_API_ROLE', value: 'accelerator_api' }
            { name: 'API_DATABASE_WORKER_ROLE', value: 'accelerator_worker' }
          ]
        }
      ]
    }
  }
}

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
  migrations: migrationJobName
}
output apiInternalUrl string = deployApplications ? apiInternalUrl : ''
output webUrl string = deployApplications ? 'https://${apps[1]!.properties.configuration.ingress!.fqdn}' : ''
