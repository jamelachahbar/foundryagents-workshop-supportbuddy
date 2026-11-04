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
$script:continueAnswers = [System.Collections.Generic.Queue[string]]::new()
foreach ($answer in $options.continue) { $script:continueAnswers.Enqueue($answer) }
$script:available = @{
    uv = $options.installed; az = $options.installed; azd = $options.installed
    pwsh = ($options.installed -and $options.pwsh)
}
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
    $script:recorded.Add(@{ name = $Name; arguments = $Arguments; venv = $env:UV_PROJECT_ENVIRONMENT })
    $global:LASTEXITCODE = 0
    if ("$Name $($Arguments -join ' ')" -eq $options.fail) {
        $global:LASTEXITCODE = 7
    }
}

function winget {
    Record-Command winget $args
    if ($global:LASTEXITCODE -eq 0) {
        $name = @{
            'astral-sh.uv' = 'uv'; 'Microsoft.AzureCLI' = 'az'; 'Microsoft.Azd' = 'azd'
            'Microsoft.PowerShell' = 'pwsh'
        }[$args[2]]
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
    if ($Prompt -like '*Continue to checkpoint*') {
        if ($script:continueAnswers.Count) { return $script:continueAnswers.Dequeue() }
        return 'Y'
    }
    if ($Prompt -like '*tenant*') { return $options.tenant }
    if ($options.saveEnv) {
        Set-Content -LiteralPath '.env' -Value 'AZURE_AI_PROJECT_ENDPOINT=https://example.invalid'
    }
    return ''
}

$failure = $null
try {
    $scriptArguments = @{}
    if ($options.packageIndex) { $scriptArguments.PackageIndexUrl = $options.packageIndex }
    if ($options.venvPath) { $scriptArguments.VenvPath = $options.venvPath }
    if ($options.prerequisitesOnly) { $scriptArguments.PrerequisitesOnly = $true }
    . $env:SETUP_TEST_SCRIPT @scriptArguments
}
catch { $failure = $_.Exception.Message }
$result = @{
    error = $failure
    commands = @($script:recorded.ToArray())
    packageIndex = $env:UV_DEFAULT_INDEX
    nativeTls = $env:UV_NATIVE_TLS
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
            "saveEnv": True, "packageIndex": "", "continue": ["Y"], "venvPath": "",
            "prerequisitesOnly": False, "pwsh": True,
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
    assert "<" not in (src / ".env").read_text()
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
    assert "[FAIL]" in result["output"]
    assert result["output"].count("[PASS]") == int(result["output"].split("/5]")[-2][-1]) - 1


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


def test_checkpoints_report_success_in_order(run_setup):
    run, _, _ = run_setup
    result = run()
    stages = [
        'Prerequisite tools (uv, az, azd)',
        'Python 3.12, lab packages, and Foundry extension',
        'Environment configuration (.env)',
        'Azure sign-in',
        'Lab preflight',
    ]
    positions = []
    for number, stage in enumerate(stages, 1):
        positions.extend([
            result["output"].index(f"[{number}/5] {stage}"),
            result["output"].index(f"[PASS] {stage}"),
        ])
    assert positions == sorted(positions)
    assert "[FAIL]" not in result["output"]


def test_user_can_stop_after_a_successful_checkpoint(run_setup):
    run, _, _ = run_setup
    result = run(**{"continue": ["N"]})
    assert result["error"] is None
    assert "[PASS] Prerequisite tools" in result["output"]
    assert "Setup stopped after checkpoint 1." in result["output"]
    assert "[2/5]" not in result["output"]
    assert commands(result) == []


def test_invalid_continue_choice_is_reprompted(run_setup):
    run, _, _ = run_setup
    result = run(**{"continue": ["maybe", "Y"]})
    assert result["error"] is None
    assert "Enter Y to continue or N to stop." in result["output"]


def test_unedited_environment_stops_before_sign_in(run_setup):
    run, src, _ = run_setup
    (src / ".env").unlink()
    result = run(saveEnv=False)
    assert "still contains placeholders" in result["error"]
    assert "[FAIL] Environment configuration" in result["output"]
    assert "[4/5]" not in result["output"]
    assert result["restoredLocation"]
    assert not any(name == "az" for name, _ in commands(result))


def test_approved_package_index_is_configured_for_uv(run_setup):
    run, _, _ = run_setup
    index = "https://packages.example.invalid/pypi/simple"
    result = run(packageIndex=index)
    assert result["error"] is None
    assert result["packageIndex"] == index
    assert result["nativeTls"] == "true"


@pytest.mark.parametrize("venv_path", ["", ".venv2", "windows env's"])
def test_selected_venv_is_used_and_printed_for_new_terminals(run_setup, venv_path):
    run, src, _ = run_setup
    result = run(venvPath=venv_path)
    assert result["error"] is None
    expected = str(src / (venv_path or ".venv-windows"))
    uv_commands = [item for item in result["commands"] if item["name"] == "uv"]
    assert len(uv_commands) == 2
    assert all(item["venv"] == expected for item in uv_commands)
    escaped = expected.replace("'", "''")
    assert f"$env:UV_PROJECT_ENVIRONMENT = '{escaped}'" in result["output"]
    assert f"Set-Location '{src}'" in result["output"]
    assert "uv run --active python main.py" in result["output"]


@pytest.mark.parametrize("index", ["http://example.invalid/simple", "not-a-url"])
def test_invalid_package_index_stops_before_setup(run_setup, index):
    run, _, _ = run_setup
    result = run(packageIndex=index)
    assert "absolute HTTPS URL" in result["error"]
    assert commands(result) == []


@pytest.mark.parametrize("installed", [False, True])
def test_instructor_prerequisites_skip_participant_configuration(run_setup, installed):
    run, src, _ = run_setup
    (src / ".env").unlink()
    result = run(prerequisitesOnly=True, installed=installed, extension=False, tenant="")
    assert result["error"] is None
    assert result["restoredLocation"]
    assert not (src / ".env").exists()
    assert "[1/2]" in result["output"]
    assert "[2/2]" in result["output"]
    assert "[3/" not in result["output"]
    assert "Local prerequisites complete" in result["output"]
    assert not any(name in {"az", "notepad.exe"} for name, _ in commands(result))
    assert [arguments for name, arguments in commands(result) if name == "uv"] == [
        ["sync", "--python", "3.12"],
    ]
    assert ("azd", ["ext", "install", "microsoft.foundry"]) in commands(result)
    if not installed:
        assert ("winget", ["install", "--id", "Microsoft.PowerShell", "--exact"]) in commands(result)


def test_instructor_prerequisites_install_missing_powershell(run_setup):
    run, _, _ = run_setup
    result = run(prerequisitesOnly=True, pwsh=False)
    assert result["error"] is None
    assert commands(result)[0] == ("winget", ["install", "--id", "Microsoft.PowerShell", "--exact"])


def test_instructor_prerequisites_print_selected_environment_and_feed(run_setup):
    run, src, existing = run_setup
    index = "https://packages.example.invalid/pypi/simple"
    result = run(prerequisitesOnly=True, venvPath=".venv2", packageIndex=index)
    assert result["error"] is None
    assert (src / ".env").read_text() == existing
    assert f"Set-Location '{src.parent / 'instructor'}'" in result["output"]
    assert f"$env:UV_PROJECT_ENVIRONMENT = '{src / '.venv2'}'" in result["output"]
    assert f"$env:UV_DEFAULT_INDEX = '{index}'" in result["output"]


def test_instructor_can_stop_before_dependency_installation(run_setup):
    run, _, _ = run_setup
    result = run(prerequisitesOnly=True, **{"continue": ["N"]})
    assert result["error"] is None
    assert commands(result) == []
    assert "Local prerequisites complete" not in result["output"]


def test_instructor_dependency_failure_prevents_completion(run_setup):
    run, _, _ = run_setup
    result = run(prerequisitesOnly=True, fail="uv sync --python 3.12")
    assert "exit code 7" in result["error"]
    assert "Local prerequisites complete" not in result["output"]
    assert result["restoredLocation"]
