"""Fresh instructor deployments include shared tracing without exposing credentials."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instructor_setup", ROOT / "instructor" / "setup_lab.py")
assert SPEC is not None and SPEC.loader is not None
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


@pytest.fixture
def values():
    return {
        "AZURE_AI_PROJECT_ENDPOINT": "https://lab.services.ai.azure.com/api/projects/supportbuddy",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model-router",
        "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME": "supportbuddy-judge",
        "AZURE_AI_EMBEDDING_DEPLOYMENT_NAME": "supportbuddy-embedding",
        "AZURE_AI_SEARCH_ENDPOINT": "https://lab.search.windows.net/",
        "FOUNDRY_PROJECT_ID": "/subscriptions/test/resourceGroups/lab/providers/Microsoft.CognitiveServices/accounts/lab/projects/supportbuddy",
        "SUPPORTBUDDY_GUARDRAIL_ID": "/subscriptions/test/resourceGroups/lab/providers/Microsoft.CognitiveServices/accounts/lab/raiPolicies/supportbuddy-guardrail",
        "APPLICATIONINSIGHTS_RESOURCE_ID": "/subscriptions/test/resourceGroups/lab/providers/Microsoft.Insights/components/lab-insights",
    }


@pytest.fixture
def arm(monkeypatch):
    credential = Mock()
    credential.get_token.return_value = SimpleNamespace(token="synthetic-arm-token")
    monkeypatch.setattr(setup, "AzureCliCredential", lambda: credential)
    get = Mock(return_value=httpx.Response(
        200, json={"properties": {"ConnectionString": "synthetic-connection-value"}},
        request=httpx.Request("GET", "https://management.azure.com/test"),
    ))
    put = Mock(return_value=httpx.Response(
        200, request=httpx.Request("PUT", "https://management.azure.com/test"),
    ))
    monkeypatch.setattr(setup.httpx, "get", get)
    monkeypatch.setattr(setup.httpx, "put", put)
    return get, put


@pytest.mark.parametrize("network_url", ["", "https://external.example/mcp"])
def test_connections_include_tracing_and_are_idempotent(values, arm, capsys, network_url):
    values["NETWORK_MCP_URL"] = network_url
    get, put = arm
    setup.create_connections(values)
    setup.create_connections(values)
    expected_count = 3 if network_url else 2
    assert get.call_count == 2
    assert put.call_count == expected_count * 2
    assert get.call_args.args[0] == (
        f"{setup.ARM}{values['APPLICATIONINSIGHTS_RESOURCE_ID']}?api-version={setup.INSIGHTS_API}"
    )
    urls = [call.args[0] for call in put.call_args_list]
    assert urls[:expected_count] == urls[expected_count:]
    first = put.call_args_list[0]
    assert first.args[0].endswith("/connections/supportbuddy-appinsights?api-version=2025-06-01")
    assert first.kwargs["json"]["properties"] == {
        "category": "AppInsights",
        "authType": "ApiKey",
        "target": values["APPLICATIONINSIGHTS_RESOURCE_ID"],
        "credentials": {"key": "synthetic-connection-value"},
        "metadata": {"ResourceId": values["APPLICATIONINSIGHTS_RESOURCE_ID"]},
    }
    assert any("supportbuddy-runbooks-kb" in url for url in urls)
    assert any("supportbuddy-network-mcp" in url for url in urls) is bool(network_url)
    output = capsys.readouterr().out
    assert "connection supportbuddy-appinsights: ok" in output
    assert "synthetic-connection-value" not in output
    assert "synthetic-arm-token" not in output


@pytest.mark.parametrize("connection", [None, "", " ", 42])
def test_missing_insights_connection_value_fails_before_writing_connections(values, arm, connection):
    get, put = arm
    get.return_value = httpx.Response(
        200, json={"properties": {"ConnectionString": connection}},
        request=httpx.Request("GET", "https://management.azure.com/test"),
    )
    with pytest.raises(ValueError, match="project tracing cannot be configured"):
        setup.create_connections(values)
    put.assert_not_called()


def test_insights_lookup_failure_is_not_reported_as_success(values, arm, capsys):
    get, put = arm
    get.return_value = httpx.Response(403, request=httpx.Request("GET", "https://management.azure.com/test"))
    with pytest.raises(httpx.HTTPStatusError):
        setup.create_connections(values)
    put.assert_not_called()
    assert "ok" not in capsys.readouterr().out


def test_project_connection_failure_is_not_reported_as_success(values, arm, capsys):
    _, put = arm
    put.return_value = httpx.Response(403, request=httpx.Request("PUT", "https://management.azure.com/test"))
    with pytest.raises(httpx.HTTPStatusError):
        setup.create_connections(values)
    assert "ok" not in capsys.readouterr().out


def test_azd_values_includes_monitoring_from_fresh_hook_environment(monkeypatch, values):
    for key in [*setup.REQUIRED, "NETWORK_MCP_URL", "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    run = Mock()
    monkeypatch.setattr(setup.subprocess, "run", run)
    assert setup.azd_values() == values
    run.assert_not_called()


def test_missing_monitoring_output_requests_reprovision(monkeypatch, values):
    for key in [*setup.REQUIRED, "NETWORK_MCP_URL", "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME"]:
        monkeypatch.delenv(key, raising=False)
    values.pop("APPLICATIONINSIGHTS_RESOURCE_ID")
    monkeypatch.setattr(setup.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=json.dumps(values))))
    with pytest.raises(SystemExit, match="APPLICATIONINSIGHTS_RESOURCE_ID.*Run `azd provision`"):
        setup.azd_values()


def test_handout_enables_tracing_without_payload_capture_or_credentials(monkeypatch, values, capsys):
    monkeypatch.setattr(setup, "azd_values", lambda: values)
    connections, scripts = Mock(), Mock()
    monkeypatch.setattr(setup, "create_connections", connections)
    monkeypatch.setattr(setup, "run_lab_script", scripts)
    assert setup.main() == 0
    connections.assert_called_once_with(values)
    assert scripts.call_count == 2
    output = capsys.readouterr().out
    assert "AZURE_AI_MODEL_DEPLOYMENT_NAME=model-router\n" in output
    assert "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME=supportbuddy-judge\n" in output
    assert "SUPPORTBUDDY_TRACING=true\n" in output
    assert "SUPPORTBUDDY_TRACE_CONTENT=false\n" in output
    assert values["APPLICATIONINSIGHTS_RESOURCE_ID"] in output
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" not in output
    assert "InstrumentationKey=" not in output


def test_optional_judge_output_is_loaded_from_hook_environment(monkeypatch, values):
    for key in [*setup.REQUIRED, "NETWORK_MCP_URL", "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME"]:
        monkeypatch.delenv(key, raising=False)
    values["AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME"] = "supportbuddy-judge"
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    run = Mock()
    monkeypatch.setattr(setup.subprocess, "run", run)
    assert setup.azd_values() == values
    run.assert_not_called()


def test_optional_judge_output_is_loaded_from_saved_azd_values(monkeypatch, values):
    for key in [*setup.REQUIRED, "NETWORK_MCP_URL", "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME"]:
        monkeypatch.delenv(key, raising=False)
    values["AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME"] = "supportbuddy-judge"
    monkeypatch.setattr(setup.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=json.dumps(values))))
    assert setup.azd_values() == values


@pytest.mark.parametrize("judge", ["", "supportbuddy-judge"])
def test_handout_includes_judge_only_when_configured(monkeypatch, values, capsys, judge):
    values["AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME"] = judge
    monkeypatch.setattr(setup, "azd_values", lambda: values)
    monkeypatch.setattr(setup, "create_connections", Mock())
    monkeypatch.setattr(setup, "run_lab_script", Mock())
    assert setup.main() == 0
    output = capsys.readouterr().out
    assert "AZURE_AI_MODEL_DEPLOYMENT_NAME=model-router\n" in output
    if judge:
        assert "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME=supportbuddy-judge\n" in output
    else:
        assert "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME=" not in output
