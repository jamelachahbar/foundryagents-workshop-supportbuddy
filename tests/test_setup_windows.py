"""Exercise the PowerShell setup without installing tools or signing in."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "setup-windows.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell is not installed")

HARNESS = r"""
$ErrorActionPreference = 'Stop'
$options = $env:SETUP_TEST_OPTIONS | ConvertFrom-Json
$script:recorded = [System.Collections.Generic.List[object]]::new()
$script:available = @{ uv = $options.installed; az = $options.installed; azd = $options.installed }
$originalLocation = (Get-Location).Path
$env:OS = $options.os

function Get-Command {
    [CmdletBinding()]
    param([string]$Name)
    if ($script:available.ContainsKey($Name)) {
        if ($script:available[$Name]) { return [pscustomobject]@{ Name = $Name } }
        return
    }
    if ($Name -eq 'winget') {
        if ($options.winget) { return [pscustomobject]@{ Name = 'winget' } }
        return
    }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}

function Record-Command {
    param([string]$Name, [string[]]$Arguments)
    $script:recorded.Add(@{ name = $Name; arguments = $Arguments })
    $global:LASTEXITCODE = 0
    if ("$Name $($Arguments -join ' ')" -eq $options.fail) {
        $global:LASTEXITCODE = 7
    }
}

function winget {
    Record-Command winget $args
    if ($global:LASTEXITCODE -eq 0) {
        $name = @{ 'astral-sh.uv' = 'uv'; 'Microsoft.AzureCLI' = 'az'; 'Microsoft.Azd' = 'azd' }[$args[2]]
        if (-not $options.stalePath) { $script:available[$name] = $true }
    }
}
function uv { Record-Command uv $args }
function az { Record-Command az $args }
function azd {
    Record-Command azd $args
    if ($args[1] -eq 'list') {
        if ($options.extension) { '[{"id":"microsoft.foundry","installedVersion":"1.0.0"}]' }
        else { '[]' }
    }
}
function Start-Process {
    param([string]$FilePath, [string]$ArgumentList)
    $script:recorded.Add(@{ name = $FilePath; arguments = @($ArgumentList) })
}
function Read-Host {
    param([string]$Prompt)
    if ($Prompt -like '*tenant*') { return $options.tenant }
    return ''
}

$failure = $null
try { . $env:SETUP_TEST_SCRIPT }
catch { $failure = $_.Exception.Message }
$result = @{
    error = $failure
    commands = @($script:recorded.ToArray())
    restoredLocation = ((Get-Location).Path -eq $originalLocation)
}
'SETUP_RESULT:' + (ConvertTo-Json -InputObject $result -Depth 6 -Compress)
"""


@pytest.fixture
def run_setup(tmp_path):
    root = tmp_path / "checkout with spaces"
    src = root / "src"
    src.mkdir(parents=True)
    shutil.copyfile(SCRIPT, root / SCRIPT.name)
    (src / ".env.template").write_text(
        "AZURE_AI_PROJECT_ENDPOINT=https://<resource>.example.invalid\nWORKSHOP_RESOURCE_PREFIX=lab-<alias>\n"
    )
    existing = "AZURE_AI_PROJECT_ENDPOINT=https://example.invalid\nWORKSHOP_RESOURCE_PREFIX=lab-test\n"
    (src / ".env").write_text(existing)

    def run(**overrides):
        options = {
            "os": "Windows_NT", "installed": True, "extension": True, "winget": True,
            "fail": "", "stalePath": False, "tenant": "test-tenant",
            **overrides,
        }
        result = subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-Command", HARNESS],
            env={
                **os.environ,
                "SETUP_TEST_OPTIONS": json.dumps(options),
                "SETUP_TEST_SCRIPT": str(root / SCRIPT.name),
            },
            capture_output=True, text=True, timeout=30, check=True,
        )
        assert "SETUP_RESULT:" in result.stdout, result.stdout + result.stderr
        report = json.loads(result.stdout.split("SETUP_RESULT:", 1)[1])
        report["output"] = result.stdout
        return report

    return run, src, existing


def commands(report):
    return [(item["name"], item["arguments"]) for item in report["commands"]]


def test_installed_tools_and_configuration_are_reused(run_setup):
    run, src, existing = run_setup
    result = run()
    assert result["error"] is None
    assert result["restoredLocation"]
    assert (src / ".env").read_text() == existing
    assert commands(result) == [
        ("uv", ["sync", "--python", "3.12"]),
        ("azd", ["ext", "list", "--installed", "--output", "json"]),
        ("az", ["login", "--tenant", "test-tenant"]),
        ("uv", ["run", "--no-sync", "python", "preflight.py"]),
    ]
    assert "Setup complete" in result["output"]


def test_missing_tools_and_extension_are_installed(run_setup):
    run, _, _ = run_setup
    result = run(installed=False, extension=False)
    assert result["error"] is None
    assert commands(result)[:3] == [
        ("winget", ["install", "--id", "astral-sh.uv", "--exact"]),
        ("winget", ["install", "--id", "Microsoft.AzureCLI", "--exact"]),
        ("winget", ["install", "--id", "Microsoft.Azd", "--exact"]),
    ]
    assert ("azd", ["ext", "install", "microsoft.foundry"]) in commands(result)


@pytest.mark.parametrize("existing_template", [False, True])
def test_configuration_is_created_or_opened_for_editing(run_setup, existing_template):
    run, src, _ = run_setup
    if existing_template:
        shutil.copyfile(src / ".env.template", src / ".env")
    else:
        (src / ".env").unlink()
    result = run()
    assert result["error"] is None
    assert (src / ".env").read_text() == (src / ".env.template").read_text()
    assert ("notepad.exe", [f'"{src / ".env"}"']) in commands(result)


@pytest.mark.parametrize("command", [
    "winget install --id astral-sh.uv --exact",
    "uv sync --python 3.12",
    "azd ext list --installed --output json",
    "azd ext install microsoft.foundry",
    "az login --tenant test-tenant",
    "uv run --no-sync python preflight.py",
])
def test_command_failures_stop_setup(run_setup, command):
    run, _, _ = run_setup
    result = run(fail=command, installed=False, extension=False)
    assert "exit code 7" in result["error"]
    assert result["restoredLocation"]
    assert "Setup complete" not in result["output"]
    last_name, last_args = commands(result)[-1]
    assert f"{last_name} {' '.join(last_args)}" == command


@pytest.mark.parametrize("options,message", [
    ({"installed": False, "winget": False}, "winget is unavailable"),
    ({"installed": False, "stalePath": True}, "Open a new PowerShell window"),
    ({"tenant": ""}, "A tenant ID is required"),
    ({"os": "Linux"}, "This script is for Windows"),
])
def test_actionable_errors(run_setup, options, message):
    run, _, _ = run_setup
    result = run(**options)
    assert message in result["error"]
    assert result["restoredLocation"]
    assert "Setup complete" not in result["output"]
