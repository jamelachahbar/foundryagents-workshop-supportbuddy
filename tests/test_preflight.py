"""Preflight checks with no CLI execution or Azure calls."""

import json
import subprocess
from unittest.mock import Mock

import pytest

import preflight


@pytest.fixture
def azd_run(monkeypatch):
    run = Mock()
    run.side_effect = [
        subprocess.CompletedProcess(["azd", "version"], 0, "azd version 1.27.1 (commit test)\n"),
        subprocess.CompletedProcess(
            ["azd", "ext", "list"], 0,
            json.dumps([{"id": "microsoft.foundry", "installedVersion": "1.0.0-beta.2"}]),
        ),
    ]
    monkeypatch.setattr(preflight.subprocess, "run", run)
    return run


def test_azd_installed(azd_run, capsys):
    assert preflight.check_azd() is None
    output = capsys.readouterr().out
    assert "[OK] Azure Developer CLI >= 1.27.1" in output
    assert "[OK] microsoft.foundry extension installed" in output
    assert "[WARN]" not in output
    assert azd_run.call_args_list[0].args[0] == ["azd", "version"]
    assert azd_run.call_args_list[1].args[0] == [
        "azd", "ext", "list", "--installed", "--output", "json",
    ]
    for call in azd_run.call_args_list:
        assert call.kwargs == {"capture_output": True, "text": True, "check": True, "timeout": 10}


def test_azd_missing(azd_run, capsys):
    azd_run.side_effect = FileNotFoundError("azd not found")
    preflight.check_azd()
    output = capsys.readouterr().out
    assert output.count("[WARN]") == 2
    assert "[FAIL]" not in output
    assert "azd not found" in output
    assert "Install or update Azure Developer CLI" in output
    assert "azd ext install microsoft.foundry" in output


@pytest.mark.parametrize("version,ok", [
    ("1.27.0", False), ("1.9.9", False), ("1.27.1", True),
    ("1.27.1-beta.1", False), ("1.27.2", True), ("1.28.0", True), ("2.0.0", True),
])
def test_azd_version_threshold(azd_run, capsys, version, ok):
    azd_run.side_effect = [
        subprocess.CompletedProcess([], 0, f"azd version {version} (commit test)"),
        subprocess.CompletedProcess([], 0, "[]"),
    ]
    preflight.check_azd()
    output = capsys.readouterr().out
    assert f"[{'OK' if ok else 'WARN'}] Azure Developer CLI >= 1.27.1" in output
    assert "[FAIL]" not in output
    if not ok:
        assert f"Detected azd version {version}" in output


@pytest.mark.parametrize("extensions", [
    [], [{"id": "another.extension", "installedVersion": "1.0.0"}],
    [{"id": "microsoft.foundry", "version": "1.0.0", "installedVersion": ""}],
    [{"id": "microsoft.foundry", "installedVersion": None}],
])
def test_foundry_extension_missing_or_only_available(azd_run, capsys, extensions):
    azd_run.side_effect = [
        subprocess.CompletedProcess([], 0, "azd version 1.27.1"),
        subprocess.CompletedProcess([], 0, json.dumps(extensions)),
    ]
    preflight.check_azd()
    output = capsys.readouterr().out
    assert "[OK] Azure Developer CLI" in output
    assert "[WARN] microsoft.foundry extension installed" in output
    assert "azd ext install microsoft.foundry" in output


@pytest.mark.parametrize("output", ["not JSON", "{}", "[null]"])
def test_invalid_extension_output_warns(azd_run, capsys, output):
    azd_run.side_effect = [
        subprocess.CompletedProcess([], 0, "azd version 1.27.1"),
        subprocess.CompletedProcess([], 0, output),
    ]
    preflight.check_azd()
    assert "Could not verify installed extensions" in capsys.readouterr().out


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, ["azd"]),
    subprocess.TimeoutExpired(["azd"], 10),
    PermissionError("azd is not executable"),
])
def test_cli_errors_warn(azd_run, capsys, error):
    azd_run.side_effect = error
    preflight.check_azd()
    output = capsys.readouterr().out
    assert output.count("[WARN]") == 2
    assert str(error) in output
    assert "[FAIL]" not in output


def test_unrecognized_version_warns(azd_run, capsys):
    azd_run.side_effect = [
        subprocess.CompletedProcess([], 0, "unknown version"),
        subprocess.CompletedProcess([], 0, "[]"),
    ]
    preflight.check_azd()
    assert "Unrecognized version output" in capsys.readouterr().out


@pytest.mark.parametrize("signed_in,answered,expected", [
    (True, True, 0), (False, True, 1), (True, False, 1),
])
def test_azd_warnings_do_not_change_readiness(azd_run, monkeypatch, capsys, signed_in, answered, expected):
    azd_run.side_effect = FileNotFoundError("azd not found")
    monkeypatch.setattr(preflight, "load_settings", lambda: {
        "WORKSHOP_RESOURCE_PREFIX": "lab-test",
        "AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test",
    })
    credential = Mock()
    if not signed_in:
        credential.get_token.side_effect = RuntimeError("Not signed in")
    monkeypatch.setattr(preflight, "AzureCliCredential", lambda: credential)

    async def model_answers(settings, credential):
        return answered

    monkeypatch.setattr(preflight, "model_answers", model_answers)
    assert preflight.main() == expected
    output = capsys.readouterr().out
    assert output.count("[WARN]") == 2
    assert ("Ready for the lab." in output) is (expected == 0)
