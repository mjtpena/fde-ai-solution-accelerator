param(
    [string] $SubscriptionId,

    [string] $Location,

    [string] $ParameterFile = 'infrastructure/parameters/dev.example.bicepparam',

    [switch] $BuildOnly
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$templatePath = Join-Path $repoRoot 'infrastructure\main.bicep'
$resolvedParameterFile = Join-Path $repoRoot $ParameterFile

if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
    throw "Bicep template not found: $templatePath"
}

if (-not (Test-Path -LiteralPath $resolvedParameterFile -PathType Leaf)) {
    throw "Bicep parameter file not found: $resolvedParameterFile"
}

az bicep lint --file $templatePath
if ($LASTEXITCODE -ne 0) {
    throw 'Bicep lint failed.'
}

az bicep build --file $templatePath --stdout | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Bicep build failed.'
}

if ($BuildOnly) {
    return
}

if ([string]::IsNullOrWhiteSpace($SubscriptionId)) {
    throw 'SubscriptionId is required unless -BuildOnly is specified.'
}

if ([string]::IsNullOrWhiteSpace($Location)) {
    throw 'Location is required unless -BuildOnly is specified.'
}

if ([string]::IsNullOrWhiteSpace($env:AZURE_POSTGRES_ADMIN_OBJECT_ID) -or
    [string]::IsNullOrWhiteSpace($env:AZURE_POSTGRES_ADMIN_NAME) -or
    [string]::IsNullOrWhiteSpace($env:AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE)) {
    throw 'Set AZURE_POSTGRES_ADMIN_OBJECT_ID, AZURE_POSTGRES_ADMIN_NAME, and AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE before running what-if.'
}

$postgresAdminObjectId = [guid]::Empty
if (-not [guid]::TryParse($env:AZURE_POSTGRES_ADMIN_OBJECT_ID, [ref] $postgresAdminObjectId)) {
    throw 'AZURE_POSTGRES_ADMIN_OBJECT_ID must be a valid Entra object ID.'
}

if ($env:AZURE_POSTGRES_ADMIN_NAME -eq 'replace-with-entra-admin') {
    throw 'Set AZURE_POSTGRES_ADMIN_NAME to the Entra administrator principal name.'
}

az deployment sub what-if `
    --subscription $SubscriptionId `
    --location $Location `
    --name "fde-dev-what-if-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))" `
    --template-file $templatePath `
    --parameters $resolvedParameterFile
if ($LASTEXITCODE -ne 0) {
    throw 'Subscription-level deployment what-if failed.'
}
