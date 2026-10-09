$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Assert-Condition {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Failure {
    param([scriptblock] $Action, [string] $ExpectedMessage)
    try {
        & $Action
    } catch {
        Assert-Condition ($_.Exception.Message -like "*$ExpectedMessage*") "Unexpected failure: $_"
        return
    }
    throw "Expected failure containing: $ExpectedMessage"
}

$compiled = az bicep build --file (Join-Path $root 'main.bicep') --stdout
Assert-Condition ($LASTEXITCODE -eq 0) 'Root Bicep compilation failed.'
$template = ($compiled -join "`n") | ConvertFrom-Json -Depth 100
$modules = @($template.resources | Where-Object type -eq 'Microsoft.Resources/deployments')
$appResources = @($modules.properties.template.resources | Where-Object type -eq 'Microsoft.App/containerApps')
Assert-Condition ($appResources.Count -eq 1) 'Expected the three-workload Container Apps resource loop.'
Assert-Condition ($appResources[0].copy.count -eq "[length(variables('workloads'))]") 'Apps must iterate all workloads.'
$appTemplate = $modules.properties.template | Where-Object {
    @($_.resources | Where-Object type -eq 'Microsoft.App/containerApps').Count -gt 0
}
Assert-Condition ($appTemplate.variables.workloads.Count -eq 3) 'Expected API, web, and worker.'
Assert-Condition ($appResources[0].identity.type -eq 'UserAssigned') 'Workload UAMIs must be attached.'
Assert-Condition ($appResources[0].properties.configuration.registries[0].identity -match 'identityId') 'Registry must use the workload identity.'
$postgres = $modules.properties.template.resources | Where-Object type -eq 'Microsoft.DBforPostgreSQL/flexibleServers'
Assert-Condition ($postgres.properties.network.publicNetworkAccess -eq 'Disabled') 'PostgreSQL must reject public access.'
Assert-Condition ($postgres.properties.network.delegatedSubnetResourceId -match 'delegatedSubnetId') 'Private PostgreSQL subnet is missing.'
Assert-Condition ($postgres.properties.network.privateDnsZoneArmResourceId -match 'privateDnsZoneId') 'Private PostgreSQL DNS is missing.'
$environment = $modules.properties.template.resources | Where-Object type -eq 'Microsoft.App/managedEnvironments'
Assert-Condition ($environment.properties.vnetConfiguration.infrastructureSubnetId -match 'infrastructureSubnetId') 'ACA must use the shared VNet.'
az bicep build-params --file (Join-Path $root 'parameters\dev.example.bicepparam') --stdout | Out-Null
Assert-Condition ($LASTEXITCODE -eq 0) 'Dev parameters failed to compile.'

$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('fde-infrastructure-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
$previousEnvironment = @{}
$environmentNames = @(
    'AZURE_POSTGRES_ADMIN_OBJECT_ID', 'AZURE_POSTGRES_ADMIN_NAME', 'AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE',
    'DEPLOY_APPLICATIONS', 'API_IMAGE', 'WEB_IMAGE', 'WORKER_IMAGE', 'API_ENTRA_TENANT_ID',
    'API_ENTRA_AUDIENCE', 'PGPASSWORD', 'PGSSLMODE', 'PGSSLROOTCERT', 'PYTHONIOENCODING'
)
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}

$global:FdeInfrastructureTest = @{
    azCalls = [System.Collections.Generic.List[string]]::new()
    psqlCalls = [System.Collections.Generic.List[string]]::new()
    failParameters = $false
    failWhatIf = $false
    invalidJson = $false
    failPsql = $false
}
function az {
    $global:FdeInfrastructureTest.azCalls.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
    if ($args -contains 'build-params' -and $global:FdeInfrastructureTest.failParameters) {
        $global:LASTEXITCODE = 1
    }
    if ($args -contains 'what-if') {
        Assert-Condition ($args -contains '--no-pretty-print') 'Azure CLI requires no-pretty-print for actual JSON output.'
        Assert-Condition ($env:PYTHONIOENCODING -eq 'utf-8') 'What-if must support Unicode principal names.'
        if ($global:FdeInfrastructureTest.failWhatIf) { $global:LASTEXITCODE = 1 }
        if ($global:FdeInfrastructureTest.invalidJson) { 'not JSON'; return }
        '{"status":"Succeeded","changes":[{"after":{"password":"sensitive","apiKey":"sensitive","name":"ordinary","displayName":"\uFFFD","empty":[]}}]}'
    }
    if ($args -contains 'get-access-token') { 'test-token-not-a-credential' }
}
function psql {
    $global:FdeInfrastructureTest.psqlCalls.Add(($args -join ' '))
    Assert-Condition ($env:PGSSLMODE -eq 'verify-full') 'Bootstrap must validate TLS.'
    Assert-Condition ($env:PGPASSWORD -eq 'test-token-not-a-credential') 'Bootstrap must use an Entra token.'
    $global:LASTEXITCODE = if ($global:FdeInfrastructureTest.failPsql) { 1 } else { 0 }
}

try {
    $validator = Join-Path $root 'scripts\validate-deployment.ps1'
    & $validator -BuildOnly
    Assert-Condition (@($global:FdeInfrastructureTest.azCalls | Where-Object { $_ -match 'build-params' }).Count -eq 1) 'BuildOnly must compile parameters.'
    Assert-Condition (@($global:FdeInfrastructureTest.azCalls | Where-Object { $_ -match 'what-if' }).Count -eq 0) 'BuildOnly must not call Azure.'
    $global:FdeInfrastructureTest.failParameters = $true
    Assert-Failure { & $validator -BuildOnly } 'parameter file compilation failed'
    $global:FdeInfrastructureTest.failParameters = $false
    $env:AZURE_POSTGRES_ADMIN_OBJECT_ID = '00000000-0000-0000-0000-000000000000'
    $env:AZURE_POSTGRES_ADMIN_NAME = 'test-admin'
    $env:AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE = 'ServicePrincipal'
    $env:DEPLOY_APPLICATIONS = 'false'
    Assert-Failure { & $validator -SubscriptionId test -Location test } 'empty GUID'
    $env:AZURE_POSTGRES_ADMIN_OBJECT_ID = '11111111-2222-3333-4444-555555555555'
    $env:DEPLOY_APPLICATIONS = 'true'
    $env:API_IMAGE = 'registry.example/api:latest'
    Assert-Failure { & $validator -SubscriptionId test -Location test } 'sha256 digest'
    $env:DEPLOY_APPLICATIONS = 'false'
    $outputPath = Join-Path $temporaryDirectory 'what-if.json'
    & $validator -SubscriptionId test -Location test -WhatIfOutputPath $outputPath
    Assert-Condition ($env:PYTHONIOENCODING -eq $previousEnvironment.PYTHONIOENCODING) 'What-if must restore the process encoding.'
    $outputText = Get-Content -LiteralPath $outputPath -Raw
    $output = $outputText | ConvertFrom-Json
    Assert-Condition ($outputText -notmatch 'sensitive') 'Artifact contains a sensitive field value.'
    Assert-Condition ($output.changes -is [array]) 'Artifact must preserve single-element arrays.'
    Assert-Condition ($output.changes[0].after.empty -is [array]) 'Artifact must preserve empty arrays.'
    Assert-Condition ($output.changes[0].after.name -eq 'ordinary') 'Artifact lost ordinary change data.'
    Assert-Condition ($output.changes[0].after.displayName -eq [string][char]0xFFFD) 'Artifact lost Unicode principal text.'
    $global:FdeInfrastructureTest.failWhatIf = $true
    Assert-Failure { & $validator -SubscriptionId test -Location test } 'what-if failed'
    $global:FdeInfrastructureTest.failWhatIf = $false
    $global:FdeInfrastructureTest.invalidJson = $true
    Assert-Failure { & $validator -SubscriptionId test -Location test -WhatIfOutputPath $outputPath } 'not valid JSON'
    $global:FdeInfrastructureTest.invalidJson = $false

    $certificate = Join-Path $temporaryDirectory 'fixture.pem'
    Set-Content -LiteralPath $certificate -Value 'test fixture, not used by mocked psql'
    $bootstrap = Join-Path $root 'scripts\bootstrap-postgres.ps1'
    $bootstrapArguments = @{
        HostName = 'test.postgres.database.azure.com'
        DatabaseName = 'accelerator'
        AdministratorName = 'test-admin'
        ApiPrincipalId = '11111111-2222-3333-4444-555555555555'
        WorkerPrincipalId = '22222222-3333-4444-5555-666666666666'
        CaCertificatePath = $certificate
    }
    $env:PGPASSWORD = 'previous-value'
    & $bootstrap @bootstrapArguments
    Assert-Condition ($global:FdeInfrastructureTest.psqlCalls.Count -eq 4) 'Bootstrap must create/grant and verify both database scopes.'
    Assert-Condition ($global:FdeInfrastructureTest.psqlCalls[0] -match '--dbname postgres.*bootstrap-postgres.sql') 'Entra principal creation must run in postgres.'
    Assert-Condition ($global:FdeInfrastructureTest.psqlCalls[1] -match '--dbname accelerator.*grant-postgres-schema.sql') 'Schema grants must run in the application database.'
    Assert-Condition ($env:PGPASSWORD -eq 'previous-value') 'Token must not remain in the process environment.'
    $global:FdeInfrastructureTest.psqlCalls.Clear()
    & $bootstrap @bootstrapArguments -VerifyOnly
    Assert-Condition ($global:FdeInfrastructureTest.psqlCalls.Count -eq 2) 'VerifyOnly must execute only verification SQL.'
    Assert-Condition (@($global:FdeInfrastructureTest.psqlCalls | Where-Object { $_ -match '(bootstrap-postgres.sql|grant-postgres-schema.sql)' }).Count -eq 0) 'VerifyOnly must not mutate roles.'
    $global:FdeInfrastructureTest.failPsql = $true
    Assert-Failure { & $bootstrap @bootstrapArguments -VerifyOnly } 'verification failed'
    Assert-Condition ($env:PGPASSWORD -eq 'previous-value') 'Failed verification must also clear the token.'
    Write-Output 'Infrastructure template, parameter compilation, validator and bootstrap contract tests passed.'
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name])
    }
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    Remove-Variable -Name FdeInfrastructureTest -Scope Global
}
