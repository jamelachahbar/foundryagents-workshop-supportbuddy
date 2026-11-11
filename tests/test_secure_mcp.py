"""instructor/secure_mcp.py builds the right Entra, Container Apps and Foundry calls (all mocked, no Azure)."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "instructor"))
SPEC = importlib.util.spec_from_file_location("secure_mcp", ROOT / "instructor" / "secure_mcp.py")
assert SPEC is not None and SPEC.loader is not None
secure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secure)

PROJECT = "/subscriptions/sub/resourceGroups/lab/providers/Microsoft.CognitiveServices/accounts/lab/projects/supportbuddy"
VALUES = {"FOUNDRY_PROJECT_ID": PROJECT,
          "AZURE_AI_PROJECT_ENDPOINT": "https://lab.services.ai.azure.com/api/projects/supportbuddy",
          "NETWORK_MCP_URL": "https://supportbuddy-network-mcp.example.azurecontainerapps.io/mcp"}


def reply(json=None, status=200):
    return httpx.Response(status, json=json, request=httpx.Request("GET", "https://test"))


class FakeGraph:
    """In-memory Graph: one app, its service principal, role assignments."""

    def __init__(self, existing_app=True):
        self.app = {"id": "app-object", "appId": "app-client", "appRoles": []} if existing_app else None
        self.assigned: list[dict] = []
        self.patches: list[tuple[str, dict]] = []
        self.posts: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        if path == "/applications":
            return reply({"value": [self.app] if self.app else []})
        if path == "/applications/app-object":
            return reply({"appRoles": self.app["appRoles"]})
        if path == "/servicePrincipals" and "app-client" in params["$filter"]:
            return reply({"value": [{"id": "resource-sp"}]})
        if path.endswith("/appRoleAssignedTo"):
            return reply({"value": self.assigned})
        if path == "/servicePrincipals/agent-object":
            return reply({"appId": "agent-client", "agentIdentityBlueprintId": "blueprint-client",
                          "displayName": "agent-x"})
        if path.startswith("/servicePrincipals/"):
            return reply({"appId": "project-client", "displayName": "agent-x"})
        raise AssertionError(path)

    def post(self, path, json):
        self.posts.append((path, json))
        if path == "/applications":
            self.app = {"id": "app-object", "appId": "app-client", "appRoles": []}
            return reply(self.app)
        if path.endswith("/appRoleAssignedTo"):
            self.assigned.append({"id": f"a{len(self.assigned)}", **json})
            return reply(json, 201)
        return reply({"id": "resource-sp"})

    def patch(self, path, json):
        self.patches.append((path, json))
        if "appRoles" in json:
            self.app["appRoles"] = json["appRoles"]
        return reply(status=204)

    def delete(self, path):
        self.assigned = [a for a in self.assigned if not path.endswith(a["id"])]
        return reply(status=204)


@pytest.fixture
def azure(monkeypatch):
    fake = secure.Azure.__new__(secure.Azure)
    fake.graph = FakeGraph()
    fake.arm = Mock()
    fake.container_app = {"properties": {"template": {
        "revisionSuffix": "existing-revision",
        "containers": [{"name": "main", "env": []}],
    }}}

    def arm_get(url):
        if url.startswith("/subscriptions/sub?"):
            return reply({"tenantId": "tenant"})
        if url.startswith(PROJECT):
            return reply({"identity": {"principalId": "project-object"}})
        if "authConfigs" in url:
            return reply({"properties": {"identityProviders": {"azureActiveDirectory": {"validation": {
                "allowedAudiences": ["api://app-client", "app-client"],
                "defaultAuthorizationPolicy": {"allowedApplications": ["project-client"]},
            }}}}})
        if "/containerApps/" in url:
            return reply(fake.container_app)
        raise AssertionError(url)

    fake.arm.get.side_effect = arm_get
    fake.arm.put.return_value = reply({})
    fake.arm.delete.return_value = reply(status=204)

    def arm_patch(url, json):
        fake.container_app["properties"]["template"] = json["properties"]["template"]
        return reply({})

    fake.arm.patch.side_effect = arm_patch
    monkeypatch.setattr(secure, "wait_for", lambda url, expected, seconds=240: expected)
    monkeypatch.setattr(secure, "probe", lambda url: 200)
    monkeypatch.setattr(secure, "list_toolbox_tools", lambda: None)
    return fake


def auth_body(azure):
    return next(call.kwargs["json"] for call in azure.arm.put.call_args_list if "authConfigs" in call.args[0])


def test_security_values_reads_only_required_azd_values(monkeypatch):
    monkeypatch.delenv("FOUNDRY_PROJECT_ID", raising=False)
    monkeypatch.delenv("NETWORK_MCP_URL", raising=False)
    monkeypatch.setattr(secure.subprocess, "run", Mock(return_value=Mock(stdout=json.dumps({
        "FOUNDRY_PROJECT_ID": PROJECT,
        "NETWORK_MCP_URL": VALUES["NETWORK_MCP_URL"],
    }))))

    assert secure.security_values() == {
        "FOUNDRY_PROJECT_ID": PROJECT,
        "NETWORK_MCP_URL": VALUES["NETWORK_MCP_URL"],
    }


def test_pattern_a_allows_only_project_identity(azure):
    secure.enable(azure, VALUES, token_version=1, roles=False)
    validation = auth_body(azure)["properties"]["identityProviders"]["azureActiveDirectory"]["validation"]
    assert validation["defaultAuthorizationPolicy"]["allowedApplications"] == ["project-client"]
    assert azure.graph.assigned == []
    assert azure.container_app["properties"]["template"]["containers"][0]["env"] == []


def test_pattern_b_uses_role_and_project_client_allow_list(azure):
    secure.enable(azure, VALUES, token_version=1, roles=True)
    validation = auth_body(azure)["properties"]["identityProviders"]["azureActiveDirectory"]["validation"]
    assert validation["defaultAuthorizationPolicy"]["allowedApplications"] == ["project-client"]
    assert validation["allowedAudiences"] == ["api://app-client", "app-client"]
    role = azure.graph.app["appRoles"][0]
    assert role["value"] == "Network.Read" and role["allowedMemberTypes"] == ["Application"]
    assert azure.graph.assigned[0]["principalId"] == "project-object"
    assert ("/servicePrincipals/resource-sp", {"appRoleAssignmentRequired": True}) in azure.graph.patches
    assert azure.container_app["properties"]["template"]["containers"][0]["env"] == [
        {"name": secure.ROLE_SETTING, "value": "Network.Read"}
    ]
    assert azure.container_app["properties"]["template"]["revisionSuffix"].startswith("role-")
    connection = next(call.kwargs["json"] for call in azure.arm.put.call_args_list if "/connections/" in call.args[0])
    assert connection["properties"]["authType"] == "ProjectManagedIdentity"


def test_enable_twice_does_not_duplicate(azure):
    secure.enable(azure, VALUES, token_version=1, roles=True)
    patch_count = azure.arm.patch.call_count
    secure.enable(azure, VALUES, token_version=1, roles=True)
    assert len(azure.graph.app["appRoles"]) == 1 and len(azure.graph.assigned) == 1
    assert azure.arm.patch.call_count == patch_count


def test_grant_and_revoke_agent(azure):
    secure.enable(azure, VALUES, token_version=1, roles=True)
    secure.grant_agent(azure, VALUES, "agent-object", connection=True)
    assert {a["principalId"] for a in azure.graph.assigned} == {"project-object", "agent-object"}
    connection = next(call.kwargs["json"] for call in azure.arm.put.call_args_list
                      if f"/connections/{secure.AGENT_CONNECTION}" in call.args[0])
    assert connection["properties"]["authType"] == "AgenticIdentityToken"
    assert connection["properties"]["audience"] == "api://app-client"
    assert connection["properties"]["target"] == VALUES["NETWORK_MCP_URL"]
    assert connection["properties"]["metadata"] == {
        "AgentInstanceClientId": "agent-client",
        "AgentBlueprintClientId": "blueprint-client",
    }
    auth_updates = [call.kwargs["json"]["properties"] for call in azure.arm.put.call_args_list
                    if "authConfigs" in call.args[0]]
    assert auth_updates[-1]["identityProviders"]["azureActiveDirectory"]["validation"][
        "defaultAuthorizationPolicy"]["allowedApplications"] == ["agent-client", "project-client"]
    secure.revoke_agent(azure, VALUES, "agent-object")
    assert {a["principalId"] for a in azure.graph.assigned} == {"project-object"}
    auth_updates = [call.kwargs["json"]["properties"] for call in azure.arm.put.call_args_list
                    if "authConfigs" in call.args[0]]
    assert auth_updates[-1]["identityProviders"]["azureActiveDirectory"]["validation"][
        "defaultAuthorizationPolicy"]["allowedApplications"] == ["project-client"]
    secure.revoke_agent(azure, VALUES, "agent-object")
    assert {a["principalId"] for a in azure.graph.assigned} == {"project-object"}


def test_revoke_agent_can_keep_allow_list_for_role_denial_control(azure):
    secure.enable(azure, VALUES, token_version=1, roles=True)
    secure.grant_agent(azure, VALUES, "agent-object", connection=True)

    secure.revoke_agent(azure, VALUES, "agent-object", keep_allowed_application=True)

    assert {assignment["principalId"] for assignment in azure.graph.assigned} == {"project-object"}
    auth_updates = [call.kwargs["json"]["properties"] for call in azure.arm.put.call_args_list
                    if "authConfigs" in call.args[0]]
    assert auth_updates[-1]["identityProviders"]["azureActiveDirectory"]["validation"][
        "defaultAuthorizationPolicy"]["allowedApplications"] == ["agent-client", "project-client"]


def test_set_server_role_changes_only_the_required_role(azure):
    secure.set_server_role(azure, VALUES, "Network.Write")

    template = azure.container_app["properties"]["template"]
    assert template["containers"][0]["env"] == [
        {"name": secure.ROLE_SETTING, "value": "Network.Write"}
    ]
    assert template["revisionSuffix"].startswith("role-")


def test_grant_agent_needs_enable_first(azure):
    azure.graph.app = None
    with pytest.raises(SystemExit):
        secure.grant_agent(azure, VALUES, "agent-object", connection=False)


def test_disable_removes_auth_role_check_and_connection_auth(azure):
    secure.disable(azure, VALUES)
    assert "authConfigs" in azure.arm.delete.call_args.args[0]
    assert azure.container_app["properties"]["template"]["containers"][0]["env"] == []
    connection = azure.arm.put.call_args.kwargs["json"]
    assert connection["properties"]["authType"] == "None"


def test_status_does_not_create_missing_service_principal(azure, capsys):
    original_get = azure.graph.get
    azure.graph.get = lambda path, params=None: reply({"value": []}) if path == "/servicePrincipals" else original_get(path, params)
    secure.status(azure, VALUES)
    assert "MCP service principal: not found" in capsys.readouterr().out
    assert azure.graph.posts == []


def test_status_fails_when_auth_read_is_forbidden(azure):
    original_get = azure.arm.get.side_effect
    azure.arm.get.side_effect = lambda url: reply(status=403) if "authConfigs" in url else original_get(url)
    with pytest.raises(SystemExit, match="read Container Apps authentication"):
        secure.status(azure, VALUES)
