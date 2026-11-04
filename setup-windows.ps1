#Requires -Version 5.1

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($env:OS -ne 'Windows_NT') {
    throw 'This script is for Windows. Use the manual setup instructions on other platforms.'
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

Install-MissingTool uv 'astral-sh.uv'
Install-MissingTool az 'Microsoft.AzureCLI'
Install-MissingTool azd 'Microsoft.Azd'

Push-Location (Join-Path $PSScriptRoot 'src')
try {
    Write-Host 'Creating src/.venv and installing the lab dependencies with Python 3.12...'
    Invoke-SetupCommand uv @('sync', '--python', '3.12')

    $extensions = @(Invoke-SetupCommand azd @('ext', 'list', '--installed', '--output', 'json') |
        ConvertFrom-Json)
    if (-not ($extensions | Where-Object { $_.id -eq 'microsoft.foundry' -and $_.installedVersion })) {
        Invoke-SetupCommand azd @('ext', 'install', 'microsoft.foundry')
    }

    $envFile = Join-Path (Get-Location).Path '.env'
    if (-not (Test-Path -LiteralPath $envFile)) {
        Copy-Item -LiteralPath '.env.template' -Destination $envFile
    }
    if ((Get-Content -LiteralPath $envFile -Raw) -match '(?m)^(AZURE_AI_PROJECT_ENDPOINT|AZURE_AI_MODEL_DEPLOYMENT_NAME|WORKSHOP_RESOURCE_PREFIX)=.*<') {
        Start-Process -FilePath 'notepad.exe' -ArgumentList "`"$envFile`""
        Write-Host 'Fill in .env using the instructor handout. Never add keys or passwords.'
        $null = Read-Host 'Save and close Notepad, then press Enter'
    }

    $tenant = Read-Host 'Azure tenant ID (from your instructor)'
    if ([string]::IsNullOrWhiteSpace($tenant)) {
        throw 'A tenant ID is required. Ask your instructor, then rerun setup.'
    }
    Invoke-SetupCommand az @('login', '--tenant', $tenant.Trim())
    Invoke-SetupCommand uv @('run', '--no-sync', 'python', 'preflight.py')
    Write-Host 'Setup complete. Run the lab commands from the src folder using uv run.'
}
finally {
    Pop-Location
}
