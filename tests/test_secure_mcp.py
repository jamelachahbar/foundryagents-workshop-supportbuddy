"""instructor/secure_mcp.py builds the right Entra, Container Apps and Foundry calls (all mocked, no Azure)."""

import importlib.util
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
    fake.arm.get.side_effect = lambda url: reply(
        {"tenantId": "tenant"} if url.startswith("/subscriptions/sub?") else {"identity": {"principalId": "project-object"}})
    fake.arm.put.return_value = reply({})
    fake.arm.delete.return_value = reply(status=204)
    monkeypatch.setattr(secure, "wait_for", lambda url, expected, seconds=240: expected)
    monkeypatch.setattr(secure, "probe", lambda url: 200)
    monkeypatch.setattr(secure, "list_toolbox_tools", lambda: None)
    cli = Mock(return_value=True)
    monkeypatch.setattr(secure, "run_cli", cli)
    fake.cli = cli
    return fake


def auth_body(azure):
    return next(call.kwargs["json"] for call in azure.arm.put.call_args_list if "authConfigs" in call.args[0])


def test_pattern_a_allows_only_project_identity(azure):
    secure.enable(azure, VALUES, token_version=1, roles=False)
    validation = auth_body(azure)["properties"]["identityProviders"]["azureActiveDirectory"]["validation"]
    assert validation["defaultAuthorizationPolicy"]["allowedApplications"] == ["project-client"]
    assert azure.graph.assigned == []
    assert any("--remove-env-vars" in call.args[0] for call in azure.cli.call_args_list)


def test_pattern_b_uses_role_not_allowed_apps(azure):
    secure.enable(azure, VALUES, token_version=1, roles=True)
    validation = auth_body(azure)["properties"]["identityProviders"]["azureActiveDirectory"]["validation"]
    assert "defaultAuthorizationPolicy" not in validation
    assert validation["allowedAudiences"] == ["api://app-client", "app-client"]
    role = azure.graph.app["appRoles"][0]
    assert role["value"] == "Network.Read" and role["allowedMemberTypes"] == ["Application"]
    assert azure.graph.assigned[0]["principalId"] == "project-object"
    assert ("/servicePrincipals/resource-sp", {"appRoleAssignmentRequired": True}) in azure.graph.patches
    assert any(f"{secure.ROLE_SETTING}=Network.Read" in call.args[0] for call in azure.cli.call_args_list)
    connection = next(call.kwargs["json"] for call in azure.arm.put.call_args_list if "/connections/" in call.args[0])
    assert connection["properties"]["authType"] == "ProjectManagedIdentity"


def test_enable_twice_does_not_duplicate(azure):
    secure.enable(azure, VALUES, token_version=1, roles=True)
    secure.enable(azure, VALUES, token_version=1, roles=True)
    assert len(azure.graph.app["appRoles"]) == 1 and len(azure.graph.assigned) == 1


def test_grant_and_revoke_agent(azure):
    secure.enable(azure, VALUES, token_version=1, roles=True)
    secure.grant_agent(azure, VALUES, "agent-object", connection=True)
    assert {a["principalId"] for a in azure.graph.assigned} == {"project-object", "agent-object"}
    create = next(call.args[0] for call in azure.cli.call_args_list if "connection" in call.args[0])
    assert create[create.index("--auth-type") + 1] == "agentic-identity"
    assert create[create.index("--audience") + 1] == "api://app-client"
    secure.revoke_agent(azure, "agent-object")
    assert {a["principalId"] for a in azure.graph.assigned} == {"project-object"}


def test_grant_agent_needs_enable_first(azure):
    azure.graph.app = None
    with pytest.raises(SystemExit):
        secure.grant_agent(azure, VALUES, "agent-object", connection=False)


def test_disable_removes_auth_role_check_and_connection_auth(azure):
    secure.disable(azure, VALUES)
    assert "authConfigs" in azure.arm.delete.call_args.args[0]
    assert any("--remove-env-vars" in call.args[0] for call in azure.cli.call_args_list)
    connection = azure.arm.put.call_args.kwargs["json"]
    assert connection["properties"]["authType"] == "None"
