#Requires -Version 5.1

param(
    [ValidateScript({
        $proxyUri = $_ -as [uri]
        if (-not $proxyUri -or -not $proxyUri.IsAbsoluteUri -or
            $proxyUri.Scheme -notin @('http', 'https') -or -not $proxyUri.Host) {
            throw 'Proxy must be an absolute HTTP or HTTPS URL, such as http://proxy.example.com:8080.'
        }
        $true
    })]
    [string]$Proxy,
    [ValidateScript({
        $indexUri = $_ -as [uri]
        if (-not $indexUri -or -not $indexUri.IsAbsoluteUri -or
            $indexUri.Scheme -ne 'https' -or -not $indexUri.Host) {
            throw 'PackageIndexUrl must be an absolute HTTPS URL.'
        }
        $true
    })]
    [string]$PackageIndexUrl,
    [ValidateNotNullOrEmpty()]
    [string]$VenvPath = '.venv-windows',
    [switch]$PrerequisitesOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$checkpointCount = if ($PrerequisitesOnly) { 2 } else { 5 }

if ($env:OS -ne 'Windows_NT') {
    throw 'This script is for Windows. Use the manual setup instructions on other platforms.'
}

if ($Proxy) {
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
    $env:NO_PROXY = (@($env:NO_PROXY, 'localhost', '127.0.0.1', '::1') |
        Where-Object { $_ }) -join ','
    $env:UV_NATIVE_TLS = 'true'
    Write-Host 'Using the supplied proxy and Windows certificate trust for uv in this PowerShell session.'
}

function Invoke-SetupCheckpoint {
    param([int]$Number, [string]$Name, [scriptblock]$Action)

    Write-Host "`n[$Number/$checkpointCount] $Name" -ForegroundColor Cyan
    try {
        & $Action
        Write-Host "[PASS] $Name" -ForegroundColor Green
    }
    catch {
        Write-Host "[FAIL] $Name. Later checkpoints were not run." -ForegroundColor Red
        throw
    }

    if ($Number -eq $checkpointCount) {
        return $true
    }

    do {
        $answer = Read-Host "Continue to checkpoint $($Number + 1)? [Y/N]"
        if ($answer -match '^(?i)y(es)?$') {
            return $true
        }
        if ($answer -match '^(?i)n(o)?$') {
            Write-Host "Setup stopped after checkpoint $Number." -ForegroundColor Yellow
            return $false
        }
        Write-Host 'Enter Y to continue or N to stop.'
    } while ($true)
}

function Invoke-SetupCommand {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed (exit code $LASTEXITCODE). Resolve the error above, then rerun setup."
    }
}

function Install-MissingTool {
    param([string]$Name, [string]$PackageId)

    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Write-Host "[OK] $Name is available."
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$Name is missing and winget is unavailable. Ask IT to install $PackageId, then rerun setup."
    }

    Write-Host "Installing $Name. Windows may ask for installer approval."
    Invoke-SetupCommand winget @('install', '--id', $PackageId, '--exact')

    # Installers update the persistent PATH, not this already-running PowerShell.
    $env:PATH = @(
        $env:PATH
        [Environment]::GetEnvironmentVariable('PATH', 'Machine')
        [Environment]::GetEnvironmentVariable('PATH', 'User')
    ) -join ';'
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was installed but is not on PATH. Open a new PowerShell window and rerun setup."
    }
}

$toolsCheckpoint = if ($PrerequisitesOnly) { 'Prerequisite tools (uv, az, azd, pwsh)' } else { 'Prerequisite tools (uv, az, azd)' }
if (-not (Invoke-SetupCheckpoint 1 $toolsCheckpoint {
    Install-MissingTool uv 'astral-sh.uv'
    Install-MissingTool az 'Microsoft.AzureCLI'
    Install-MissingTool azd 'Microsoft.Azd'
    if ($PrerequisitesOnly) {
        Install-MissingTool pwsh 'Microsoft.PowerShell'
    }
})) { return }

Push-Location (Join-Path $PSScriptRoot 'src')
try {
    if (-not (Invoke-SetupCheckpoint 2 'Python 3.12, lab packages, and Foundry extension' {
        if ($PackageIndexUrl) {
            $env:UV_DEFAULT_INDEX = $PackageIndexUrl
            $env:UV_NATIVE_TLS = 'true'
            Write-Host 'Using the supplied package index and Windows certificate trust for uv in this PowerShell session.'
            Write-Host 'If CFS quarantines a required version, ask IT or your instructor; do not bypass the approved feed.'
        }
        $env:UV_PROJECT_ENVIRONMENT = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($VenvPath)
        Write-Host "Creating $env:UV_PROJECT_ENVIRONMENT and installing the lab dependencies with Python 3.12..."
        Invoke-SetupCommand uv @('sync', '--python', '3.12')

        $extensions = @(Invoke-SetupCommand azd @('ext', 'list', '--installed', '--output', 'json') |
            ConvertFrom-Json)
        if (-not ($extensions | Where-Object { $_.id -eq 'microsoft.foundry' -and $_.installedVersion })) {
            Invoke-SetupCommand azd @('ext', 'install', 'microsoft.foundry')
        }
    })) { return }

    if ($PrerequisitesOnly) {
        Write-Host 'Local prerequisites complete. No .env changes, Azure sign-in, preflight, or provisioning were performed.'
        Write-Host 'Paste these commands into PowerShell 7 before following instructor/setup.md for Azure provisioning:'
        $instructorLiteral = (Join-Path $PSScriptRoot 'instructor').Replace("'", "''")
        $venvLiteral = $env:UV_PROJECT_ENVIRONMENT.Replace("'", "''")
        Write-Host "Set-Location '$instructorLiteral'"
        Write-Host "`$env:UV_PROJECT_ENVIRONMENT = '$venvLiteral'"
        if ($PackageIndexUrl) {
            Write-Host "`$env:UV_DEFAULT_INDEX = '$($PackageIndexUrl.Replace("'", "''"))'"
        }
        return
    }

    if (-not (Invoke-SetupCheckpoint 3 'Environment configuration (.env)' {
        $envFile = Join-Path (Get-Location).Path '.env'
        if (-not (Test-Path -LiteralPath $envFile)) {
            Copy-Item -LiteralPath '.env.template' -Destination $envFile
        }
        $placeholderPattern = '(?m)^(AZURE_AI_PROJECT_ENDPOINT|AZURE_AI_MODEL_DEPLOYMENT_NAME|WORKSHOP_RESOURCE_PREFIX)=.*<'
        if ((Get-Content -LiteralPath $envFile -Raw) -match $placeholderPattern) {
            Start-Process -FilePath 'notepad.exe' -ArgumentList "`"$envFile`""
            Write-Host 'Fill in .env using the instructor handout. Never add keys or passwords.'
            $null = Read-Host 'Save and close Notepad, then press Enter'
        }
        if ((Get-Content -LiteralPath $envFile -Raw) -match $placeholderPattern) {
            throw '.env still contains placeholders. Fill in the instructor values, then rerun setup.'
        }
    })) { return }

    if (-not (Invoke-SetupCheckpoint 4 'Azure sign-in' {
        $tenant = Read-Host 'Azure tenant ID (from your Azure admin/instructor)'
        if ([string]::IsNullOrWhiteSpace($tenant)) {
            throw 'A tenant ID is required. Ask your Azure admin/instructor, then rerun setup.'
        }
        Invoke-SetupCommand az @('login', '--tenant', $tenant.Trim())
    })) { return }
    $null = Invoke-SetupCheckpoint 5 'Lab preflight' {
        Invoke-SetupCommand uv @('run', '--no-sync', 'python', 'preflight.py')
    }
    Write-Host 'Setup complete. Open PowerShell in the repository root (the folder containing setup-windows.ps1), then paste:'
    $venvLiteral = $env:UV_PROJECT_ENVIRONMENT.Replace("'", "''")
    Write-Host "if (-not (Test-Path '.\setup-windows.ps1')) { throw 'Open PowerShell in the repository root first.' }"
    Write-Host "`$env:UV_PROJECT_ENVIRONMENT = '$venvLiteral'"
    if ($PackageIndexUrl) {
        Write-Host "`$env:UV_DEFAULT_INDEX = '$($PackageIndexUrl.Replace("'", "''"))'"
    }
    Write-Host 'uv run --directory src python main.py'
    Write-Host 'Do not run from the venv Scripts folder or share a venv between Windows and the devcontainer.'
    Write-Host 'To use an already activated Windows venv instead, run from the repository root: uv run --directory src --active python main.py'
}
finally {
    Pop-Location
}
