param(
    [string] $SubscriptionId,

    [string] $Location,

    [string] $ParameterFile = 'infrastructure/parameters/dev.example.bicepparam',

    [switch] $BuildOnly,

    [string] $WhatIfOutputPath
)

$ErrorActionPreference = 'Stop'

function Invoke-WhatIf {
    param([string[]] $Arguments)
    $previousEncoding = $env:PYTHONIOENCODING
    $previousConsoleEncoding = [Console]::OutputEncoding
    try {
        $env:PYTHONIOENCODING = 'utf-8'
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        & az @Arguments
    } finally {
        $env:PYTHONIOENCODING = $previousEncoding
        [Console]::OutputEncoding = $previousConsoleEncoding
    }
}

function Redact-WhatIfValue {
    param(
        [AllowNull()]
        [object] $Value
    )

    if ($null -eq $Value) {
        return $null
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $redactedValue = [ordered]@{}
        foreach ($key in $Value.Keys) {
            if ($key -match '(?i)(password|secret|token|connection.?string|shared.?key|access.?key|primary.?key|secondary.?key|api.?key|(^|[^a-z])key([^a-z]|$))') {
                $redactedValue[$key] = '[REDACTED]'
            } else {
                $redactedValue[$key] = Redact-WhatIfValue -Value $Value[$key]
            }
        }

        return $redactedValue
    }

    if ($Value -is [array]) {
        return ,@(
            foreach ($item in $Value) {
                Redact-WhatIfValue -Value $item
            }
        )
    }

    return $Value
}

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

az bicep build-params --file $resolvedParameterFile --stdout | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Bicep parameter file compilation failed.'
}

if ($BuildOnly) {
    if (-not [string]::IsNullOrWhiteSpace($WhatIfOutputPath)) {
        throw '-WhatIfOutputPath cannot be used with -BuildOnly.'
    }

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

if ($postgresAdminObjectId -eq [guid]::Empty) {
    throw 'AZURE_POSTGRES_ADMIN_OBJECT_ID cannot be the empty GUID.'
}

if ($env:DEPLOY_APPLICATIONS -eq 'true') {
    foreach ($imageVariable in @('API_IMAGE', 'WEB_IMAGE', 'WORKER_IMAGE')) {
        $image = [Environment]::GetEnvironmentVariable($imageVariable)
        if ($image -notmatch '^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$') {
            throw "$imageVariable must contain a registry image pinned by its sha256 digest."
        }
    }
    if ([string]::IsNullOrWhiteSpace($env:API_ENTRA_TENANT_ID) -or
        [string]::IsNullOrWhiteSpace($env:API_ENTRA_AUDIENCE)) {
        throw 'Set API_ENTRA_TENANT_ID and API_ENTRA_AUDIENCE before deploying applications.'
    }
}

if ($env:AZURE_POSTGRES_ADMIN_NAME -eq 'replace-with-entra-admin') {
    throw 'Set AZURE_POSTGRES_ADMIN_NAME to the Entra administrator principal name.'
}

$whatIfArgs = @(
    'deployment'
    'sub'
    'what-if'
    '--subscription'
    $SubscriptionId
    '--location'
    $Location
    '--name'
    "fde-dev-what-if-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
    '--template-file'
    $templatePath
    '--parameters'
    $resolvedParameterFile
    '--output'
    'json'
    '--no-pretty-print'
    '--no-prompt'
)

$resolvedWhatIfOutputPath = $null
if ([string]::IsNullOrWhiteSpace($WhatIfOutputPath)) {
    $whatIfOutput = Invoke-WhatIf -Arguments $whatIfArgs
} else {
    if ([System.IO.Path]::IsPathRooted($WhatIfOutputPath)) {
        $resolvedWhatIfOutputPath = [System.IO.Path]::GetFullPath($WhatIfOutputPath)
    } else {
        $resolvedWhatIfOutputPath = [System.IO.Path]::GetFullPath(
            (Join-Path $repoRoot $WhatIfOutputPath)
        )
    }

    $outputDirectory = [System.IO.Path]::GetDirectoryName($resolvedWhatIfOutputPath)
    if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    }

    $whatIfOutput = Invoke-WhatIf -Arguments $whatIfArgs
}

if ($LASTEXITCODE -ne 0) {
    throw 'Subscription-level deployment what-if failed.'
}

if (-not [string]::IsNullOrWhiteSpace($WhatIfOutputPath)) {
    try {
        $whatIfResult = ConvertFrom-Json -InputObject ($whatIfOutput -join [Environment]::NewLine) -AsHashtable -ErrorAction Stop
    } catch {
        throw 'Azure CLI what-if output was not valid JSON.'
    }

    $redactedWhatIfResult = Redact-WhatIfValue -Value $whatIfResult
    $redactedWhatIfJson = ConvertTo-Json -InputObject $redactedWhatIfResult -Depth 100
    Set-Content -LiteralPath $resolvedWhatIfOutputPath -Value $redactedWhatIfJson -Encoding utf8
} else {
    $whatIfOutput
}
