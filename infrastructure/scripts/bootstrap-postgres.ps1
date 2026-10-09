param(
    [Parameter(Mandatory)]
    [string] $HostName,
    [Parameter(Mandatory)]
    [string] $DatabaseName,
    [Parameter(Mandatory)]
    [string] $AdministratorName,
    [Parameter(Mandatory)]
    [guid] $ApiPrincipalId,
    [Parameter(Mandatory)]
    [guid] $WorkerPrincipalId,
    [Parameter(Mandatory)]
    [guid] $MigratorPrincipalId,
    [Parameter(Mandatory)]
    [string] $CaCertificatePath,
    [switch] $VerifyOnly
)

$ErrorActionPreference = 'Stop'
$principals = @($ApiPrincipalId, $WorkerPrincipalId, $MigratorPrincipalId)
if ($principals -contains [guid]::Empty) {
    throw 'Workload principal IDs cannot be empty GUIDs.'
}
if (@($principals | Select-Object -Unique).Count -ne $principals.Count) {
    throw 'API, worker and migrator must use distinct managed identities.'
}
if (-not (Test-Path -LiteralPath $CaCertificatePath -PathType Leaf)) {
    throw 'Supply the trusted PostgreSQL CA certificate bundle.'
}
Get-Command psql -ErrorAction Stop | Out-Null

$previousPassword = $env:PGPASSWORD
$previousSslMode = $env:PGSSLMODE
$previousSslRootCert = $env:PGSSLROOTCERT
try {
    $token = az account get-access-token --resource-type oss-rdbms --query accessToken --output tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw 'Could not acquire an Entra PostgreSQL token for the signed-in administrator.'
    }
    $env:PGPASSWORD = $token
    $env:PGSSLMODE = 'verify-full'
    $env:PGSSLROOTCERT = (Resolve-Path -LiteralPath $CaCertificatePath).Path
    $arguments = @(
        '--no-psqlrc', '--host', $HostName,
        '--username', $AdministratorName, '--no-password',
        '--set', 'ON_ERROR_STOP=1',
        '--set', "database_name=$DatabaseName",
        '--set', "api_principal_id=$ApiPrincipalId",
        '--set', "worker_principal_id=$WorkerPrincipalId",
        '--set', "migrator_principal_id=$MigratorPrincipalId"
    )
    if (-not $VerifyOnly) {
        & psql @arguments --dbname postgres --file (Join-Path $PSScriptRoot 'bootstrap-postgres.sql')
        if ($LASTEXITCODE -ne 0) {
            throw 'PostgreSQL workload principal bootstrap failed.'
        }
        & psql @arguments --dbname $DatabaseName --file (Join-Path $PSScriptRoot 'grant-postgres-schema.sql')
        if ($LASTEXITCODE -ne 0) {
            throw 'PostgreSQL schema grant failed.'
        }
    }
    & psql @arguments --dbname postgres --file (Join-Path $PSScriptRoot 'verify-postgres.sql')
    if ($LASTEXITCODE -ne 0) {
        throw 'PostgreSQL workload principal verification failed; do not deploy applications.'
    }
    & psql @arguments --dbname $DatabaseName --file (Join-Path $PSScriptRoot 'verify-postgres-schema.sql')
    if ($LASTEXITCODE -ne 0) {
        throw 'PostgreSQL schema privilege verification failed; do not deploy applications.'
    }
} finally {
    $token = $null
    $env:PGPASSWORD = $previousPassword
    $env:PGSSLMODE = $previousSslMode
    $env:PGSSLROOTCERT = $previousSslRootCert
}
