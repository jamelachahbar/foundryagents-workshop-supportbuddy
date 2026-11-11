"""Secure the shared network MCP server with Microsoft Entra ID (optional lab: docs/optional-secure-mcp.md).

Run from the instructor folder after `azd up`. Every command is safe to repeat.

  Pattern A - project identity (Stage 9 instructor demo):
    uv run --project ../src python secure_mcp.py enable
        Entra token required; only the Foundry project's managed identity may call the server.

  Pattern B - per-agent identity with an app role (Stage 14, recommended for service tools):
    uv run --project ../src python secure_mcp.py enable --roles
        Entra token required AND the caller needs the app role Network.Read, checked by the server.
        The project identity gets the role, so the existing toolbox keeps working.
    uv run --project ../src python secure_mcp.py grant-agent --principal-id <agent identity object ID>
        Gives one agent identity the role and creates the connection `supportbuddy-network-mcp-agent`
        (agent identity authentication) for that agent's toolbox.
    uv run --project ../src python secure_mcp.py revoke-agent --principal-id <agent identity object ID>
    uv run --project ../src python secure_mcp.py revoke-agent --principal-id <agent identity object ID> --keep-allowed-application
        Role-denial control: remove the role while keeping the platform allow-list entry.
    uv run --project ../src python secure_mcp.py set-server-role --required-role Network.Write
        Cache-independent denial control; restore Network.Read afterward.

  Both:
    uv run --project ../src python secure_mcp.py status     # what is configured; tests the endpoint
    uv run --project ../src python secure_mcp.py disable    # back to the lab default (no authentication)

Pattern C (on behalf of the signed-in user) needs preview features and consent in the tenant; it is
described in the optional lab, not automated here.

Needs: Owner or Contributor on the resource group, and the right to create app registrations in Entra ID.
Lab construct: the server only serves synthetic, read-only data. Re-running `azd provision` (or setup_lab.py)
resets the toolbox connection to no authentication and can redeploy the app without the role setting:
run `secure_mcp.py enable [--roles]` again afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from azure.identity import AzureCliCredential
from setup_lab import ARM, CONNECTIONS_API, SRC

GRAPH = "https://graph.microsoft.com/v1.0"
CONTAINER_APPS_API = "2024-03-01"
SUBSCRIPTIONS_API = "2022-12-01"
APP_NAME = "supportbuddy-network-mcp"
CONTAINER_APP = "supportbuddy-network-mcp"
CONNECTION = "supportbuddy-network-mcp"
AGENT_CONNECTION = "supportbuddy-network-mcp-agent"
ROLE = "Network.Read"
ROLE_SETTING = "NETWORK_MCP_REQUIRED_ROLE"
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "secure_mcp", "version": "1"}},
}


def security_values() -> dict[str, str]:
    """Resolve only the project and MCP endpoint needed by this security helper."""
    required = ("FOUNDRY_PROJECT_ID", "NETWORK_MCP_URL")
    values = {name: os.environ[name] for name in required if os.getenv(name)}
    if len(values) < len(required):
        result = subprocess.run(
            ["azd", "env", "get-values", "--output", "json"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
            shell=sys.platform == "win32",
        )
        values = {**json.loads(result.stdout), **values}
    missing = [name for name in required if not values.get(name)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}. Run `azd provision` first, from the instructor folder.")
    return values


class Azure:
    """Small ARM and Microsoft Graph client that signs in with the Azure CLI."""

    def __init__(self) -> None:
        credential = AzureCliCredential()
        self.arm = httpx.Client(base_url=ARM, timeout=60,
                                headers={"Authorization": f"Bearer {credential.get_token(f'{ARM}/.default').token}"})
        self.graph = httpx.Client(base_url=GRAPH, timeout=60, headers={
            "Authorization": f"Bearer {credential.get_token('https://graph.microsoft.com/.default').token}"})

    @staticmethod
    def check(response: httpx.Response, what: str) -> dict:
        if response.status_code in (401, 403):
            sys.exit(f"Not allowed to {what} ({response.status_code}). You need Owner/Contributor on the resource "
                     f"group and the right to create app registrations.\n{response.text}")
        if response.is_error:
            sys.exit(f"Failed to {what}: {response.status_code} {response.text}")
        return response.json() if response.content else {}


def probe(url: str) -> int:
    """Call the MCP endpoint with no token, as anyone on the internet could."""
    try:
        response = httpx.post(url, json=INITIALIZE, timeout=30,
                              headers={"Accept": "application/json, text/event-stream"})
        return response.status_code
    except httpx.HTTPError as error:
        print(f"  could not reach {url}: {error}")
        return 0


def wait_for(url: str, expected: int, seconds: int = 240) -> int:
    deadline = time.monotonic() + seconds
    status = probe(url)
    while status != expected and time.monotonic() < deadline:
        time.sleep(10)
        status = probe(url)
    return status


def ids(values: dict[str, str]) -> tuple[str, str, str]:
    """Subscription, resource group and Container App resource ID, derived from the project ID."""
    parts = values["FOUNDRY_PROJECT_ID"].strip("/").split("/")
    subscription, group = parts[1], parts[3]
    app_id = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.App/containerApps/{CONTAINER_APP}"
    return subscription, group, app_id


def project_identity(azure: Azure, project_id: str) -> tuple[str, str]:
    """The project's managed identity: (object ID, client ID)."""
    project = azure.check(azure.arm.get(f"{project_id}?api-version={CONNECTIONS_API}"), "read the Foundry project")
    principal_id = (project.get("identity") or {}).get("principalId")
    if not principal_id:
        sys.exit("The Foundry project has no system-assigned managed identity.")
    service_principal = azure.check(azure.graph.get(f"/servicePrincipals/{principal_id}"),
                                    "read the project's managed identity")
    return principal_id, service_principal["appId"]


def find_app(azure: Azure) -> dict | None:
    found = azure.check(azure.graph.get("/applications", params={"$filter": f"displayName eq '{APP_NAME}'"}),
                        "look up the app registration")["value"]
    return found[0] if found else None


def ensure_app_registration(azure: Azure, token_version: int) -> dict:
    app = find_app(azure)
    if app:
        print(f"  app registration {APP_NAME}: reused ({app['appId']})")
    else:
        app = azure.check(azure.graph.post("/applications", json={
            "displayName": APP_NAME, "signInAudience": "AzureADMyOrg"}), "create the app registration")
        print(f"  app registration {APP_NAME}: created ({app['appId']})")
    azure.check(azure.graph.patch(f"/applications/{app['id']}", json={
        "identifierUris": [f"api://{app['appId']}"],
        "api": {"requestedAccessTokenVersion": token_version if token_version == 2 else None},
    }), "set the app ID URI")
    return app


def ensure_app_role(azure: Azure, app: dict) -> str:
    """Add the application role Network.Read (for apps and agents, not users); return its ID."""
    current = azure.check(azure.graph.get(f"/applications/{app['id']}", params={"$select": "appRoles"}),
                          "read the app roles")
    roles = current.get("appRoles") or []
    for role in roles:
        if role.get("value") == ROLE:
            return role["id"]
    role_id = str(uuid.uuid4())
    roles.append({"id": role_id, "value": ROLE, "isEnabled": True, "allowedMemberTypes": ["Application"],
                  "displayName": "Read network diagnostics",
                  "description": "Call the read-only network diagnostic tools of the SupportBuddy MCP server."})
    azure.check(azure.graph.patch(f"/applications/{app['id']}", json={"appRoles": roles}), "add the app role")
    print(f"  app role {ROLE}: added")
    return role_id


def ensure_service_principal(azure: Azure, app_id: str, assignment_required: bool | None = None) -> str:
    """The MCP app's service principal in this tenant; optionally require role assignment for tokens."""
    existing = azure.check(azure.graph.get("/servicePrincipals", params={"$filter": f"appId eq '{app_id}'"}),
                           "look up the service principal")["value"]
    sp = existing[0] if existing else azure.check(azure.graph.post("/servicePrincipals", json={"appId": app_id}),
                                                  "create the service principal")
    if assignment_required is not None:
        # Defence in depth: Entra issues tokens for this app only to callers that were assigned a role.
        azure.check(azure.graph.patch(f"/servicePrincipals/{sp['id']}",
                                      json={"appRoleAssignmentRequired": assignment_required}),
                    "set role assignment required")
    return sp["id"]


def assignments(azure: Azure, resource_sp: str) -> list[dict]:
    return azure.check(azure.graph.get(f"/servicePrincipals/{resource_sp}/appRoleAssignedTo"),
                       "list role assignments")["value"]


def grant(azure: Azure, resource_sp: str, role_id: str, principal_id: str, label: str) -> None:
    if any(a["principalId"] == principal_id and a["appRoleId"] == role_id for a in assignments(azure, resource_sp)):
        print(f"  {label}: already has {ROLE}")
        return
    azure.check(azure.graph.post(f"/servicePrincipals/{resource_sp}/appRoleAssignedTo", json={
        "principalId": principal_id, "resourceId": resource_sp, "appRoleId": role_id}), f"grant {ROLE} to {label}")
    print(f"  {label}: granted {ROLE}")


def set_auth(azure: Azure, app_resource: str, app_id: str, issuer: str, allowed: list[str] | None) -> None:
    validation: dict = {"allowedAudiences": [f"api://{app_id}", app_id]}
    validation["defaultAuthorizationPolicy"] = {"allowedApplications": allowed} if allowed is not None else None
    azure.check(azure.arm.put(f"{app_resource}/authConfigs/current?api-version={CONTAINER_APPS_API}", json={"properties": {
        "platform": {"enabled": True},
        "globalValidation": {"unauthenticatedClientAction": "Return401"},
        "identityProviders": {"azureActiveDirectory": {
            "enabled": True,
            "registration": {"clientId": app_id, "openIdIssuer": issuer},
            "validation": validation,
        }},
    }}), "turn on Container Apps authentication")


def update_allowed_application(azure: Azure, app_resource: str, client_id: str, allow: bool) -> None:
    """Keep Easy Auth's client allow-list aligned with identities granted Network.Read."""
    path = f"{app_resource}/authConfigs/current?api-version={CONTAINER_APPS_API}"
    current = azure.check(azure.arm.get(path), "read Container Apps authentication")
    properties = current.get("properties", {})
    aad = properties.setdefault("identityProviders", {}).setdefault("azureActiveDirectory", {})
    validation = aad.setdefault("validation", {})
    policy = validation.setdefault("defaultAuthorizationPolicy", {}) or {}
    applications = set(policy.get("allowedApplications") or [])
    if allow:
        applications.add(client_id)
    else:
        applications.discard(client_id)
    validation["defaultAuthorizationPolicy"] = {"allowedApplications": sorted(applications)}
    azure.check(azure.arm.put(path, json={"properties": properties}), "update allowed Container Apps clients")


def set_required_role(azure: Azure, app_resource: str, role: str | None) -> None:
    """Set or remove the app's role check while preserving its existing container template."""
    resource_url = f"{app_resource}?api-version={CONTAINER_APPS_API}"
    resource = azure.check(azure.arm.get(resource_url), "read the Container App")
    template = resource["properties"]["template"]
    container = template["containers"][0]
    env = [entry for entry in container.get("env", []) if entry.get("name") != ROLE_SETTING]
    current_role = next((entry.get("value") for entry in container.get("env", [])
                         if entry.get("name") == ROLE_SETTING), None)
    if current_role == role:
        print(f"  server role check: {role or 'off'} (unchanged)")
        return
    if role:
        env.append({"name": ROLE_SETTING, "value": role})
    container["env"] = env
    template["revisionSuffix"] = f"role-{uuid.uuid4().hex[:10]}"
    azure.check(azure.arm.patch(resource_url, json={"properties": {"template": template}}),
                "update the server setting")
    print(f"  server role check: {role or 'off'}")


def set_server_role(azure: Azure, values: dict[str, str], role: str) -> None:
    """Set a temporary or normal server role without changing Entra assignments."""
    _, _, app_resource = ids(values)
    set_required_role(azure, app_resource, role)


def set_connection(azure: Azure, values: dict[str, str], audience: str | None) -> None:
    properties = {"category": "RemoteTool", "target": values["NETWORK_MCP_URL"],
                  **({"authType": "ProjectManagedIdentity", "audience": audience} if audience else {"authType": "None"})}
    azure.check(azure.arm.put(f"{values['FOUNDRY_PROJECT_ID']}/connections/{CONNECTION}?api-version={CONNECTIONS_API}",
                              json={"properties": properties}), "update the toolbox connection")
    print(f"  connection {CONNECTION}: {properties['authType']}" + (f" (audience {audience})" if audience else ""))


def set_agent_connection(azure: Azure, values: dict[str, str], audience: str,
                         instance_client_id: str, blueprint_client_id: str) -> None:
    properties = {
        "category": "RemoteTool",
        "target": values["NETWORK_MCP_URL"],
        "authType": "AgenticIdentityToken",
        "audience": audience,
        "metadata": {
            "AgentInstanceClientId": instance_client_id,
            "AgentBlueprintClientId": blueprint_client_id,
        },
    }
    azure.check(azure.arm.put(
        f"{values['FOUNDRY_PROJECT_ID']}/connections/{AGENT_CONNECTION}?api-version={CONNECTIONS_API}",
        json={"properties": properties},
    ), "create the agent-identity toolbox connection")
    print(f"  connection {AGENT_CONNECTION}: agent identity authentication (audience {audience})")


def list_toolbox_tools() -> None:
    if not (SRC / ".env").is_file():
        print("  no src/.env: ask a participant to run `uv run python toolbox.py show` and a Wi-Fi prompt.")
        return
    print("  listing your toolbox's tools through Foundry (uses src/.env):")
    result = subprocess.run([sys.executable, "toolbox.py", "show"], cwd=SRC, capture_output=True, text=True, check=False)
    output = (result.stdout + result.stderr).strip()
    print("    " + output.replace("\n", "\n    ") if output else "    (no output)")
    if result.returncode != 0 or "network___" not in output:
        print("  network___ tools not listed: run `uv run python toolbox.py create` in src and try again; "
              "if it still fails, retry with --token-version 2.")


def issuer_for(azure: Azure, subscription: str, token_version: int) -> str:
    tenant = azure.check(azure.arm.get(f"/subscriptions/{subscription}?api-version={SUBSCRIPTIONS_API}"),
                         "read the subscription")["tenantId"]
    return f"https://login.microsoftonline.com/{tenant}/v2.0" if token_version == 2 else f"https://sts.windows.net/{tenant}/"


def enable(azure: Azure, values: dict[str, str], token_version: int, roles: bool) -> None:
    subscription, group, app_resource = ids(values)
    url = values["NETWORK_MCP_URL"]
    print(f"1. Without a token: {probe(url)} (expect 200 before securing)")
    print("2. Foundry project identity")
    project_object, project_client = project_identity(azure, values["FOUNDRY_PROJECT_ID"])
    print(f"  project managed identity: object {project_object}, client {project_client}")
    print("3. App registration for the MCP server")
    app = ensure_app_registration(azure, token_version)
    resource_sp = ensure_service_principal(azure, app["appId"], assignment_required=True if roles else None)
    if roles:
        print(f"4. App role {ROLE} (pattern B)")
        role_id = ensure_app_role(azure, app)
        grant(azure, resource_sp, role_id, project_object, "project identity")
    else:
        print("4. App role: not used (pattern A); add --roles for per-agent access")
    issuer = issuer_for(azure, subscription, token_version)
    print("5. Container Apps authentication")
    set_auth(azure, app_resource, app["appId"], issuer, [project_client])
    print(f"  required: Entra token for api://{app['appId']} (issuer {issuer})")
    print("  allowed callers: the project identity" + (f" with {ROLE} checked by the server" if roles else " only"))
    print("6. Server role check")
    set_required_role(azure, app_resource, ROLE if roles else None)
    print("7. Toolbox connection")
    set_connection(azure, values, f"api://{app['appId']}")
    print(f"8. Without a token: {wait_for(url, 401)} (expect 401 now; changes can take a minute)")
    print("9. Through the toolbox")
    list_toolbox_tools()
    if roles:
        print("Note: a managed identity can keep using a token issued before the role assignment for up to "
              "24 hours. If the toolbox gets 403, wait and retry, or enable --roles the day before.")


def grant_agent(azure: Azure, values: dict[str, str], principal_id: str, connection: bool) -> None:
    app = find_app(azure)
    if not app:
        sys.exit("Run `secure_mcp.py enable --roles` first.")
    resource_sp = ensure_service_principal(azure, app["appId"])
    role_id = ensure_app_role(azure, app)
    agent = azure.check(azure.graph.get(f"/servicePrincipals/{principal_id}"), "read the agent identity")
    grant(azure, resource_sp, role_id, principal_id, f"agent identity {agent.get('displayName', principal_id)}")
    _, _, app_resource = ids(values)
    update_allowed_application(azure, app_resource, agent["appId"], allow=True)
    if connection:
        blueprint_client_id = agent.get("agentIdentityBlueprintId")
        if not blueprint_client_id:
            sys.exit(f"Agent identity {principal_id} has no agentIdentityBlueprintId; cannot configure the toolbox connection.")
        set_agent_connection(azure, values, f"api://{app['appId']}", agent["appId"], blueprint_client_id)
        print(f"  use {AGENT_CONNECTION} in the agent's toolbox (project_connection_id = {AGENT_CONNECTION}).")


def revoke_agent(azure: Azure, values: dict[str, str], principal_id: str,
                 keep_allowed_application: bool = False) -> None:
    app = find_app(azure)
    if not app:
        sys.exit("No app registration found; nothing to revoke.")
    agent = azure.check(azure.graph.get(f"/servicePrincipals/{principal_id}"), "read the agent identity")
    resource_sp = ensure_service_principal(azure, app["appId"])
    removed = 0
    for assignment in assignments(azure, resource_sp):
        if assignment["principalId"] == principal_id:
            response = azure.graph.delete(f"/servicePrincipals/{resource_sp}/appRoleAssignedTo/{assignment['id']}")
            if response.status_code not in (204, 404):
                azure.check(response, "remove the role assignment")
            removed += 1
    if not keep_allowed_application:
        _, _, app_resource = ids(values)
        update_allowed_application(azure, app_resource, agent["appId"], allow=False)
    else:
        print(f"  kept {agent['appId']} in the platform allow-list for the role-denial control")
    print(f"  removed {removed} role assignment(s) for {principal_id}")


def disable(azure: Azure, values: dict[str, str]) -> None:
    _, _, app_resource = ids(values)
    print("1. Container Apps authentication: off")
    response = azure.arm.delete(f"{app_resource}/authConfigs/current?api-version={CONTAINER_APPS_API}")
    if response.status_code not in (200, 202, 204, 404):
        azure.check(response, "turn off Container Apps authentication")
    print("2. Server role check")
    set_required_role(azure, app_resource, None)
    print("3. Toolbox connection")
    set_connection(azure, values, None)
    print(f"4. Without a token: {wait_for(values['NETWORK_MCP_URL'], 200)} (expect 200: lab default)")
    print(f"The app registration {APP_NAME}, its role and assignments are kept; delete them in Entra ID when done.")


def status(azure: Azure, values: dict[str, str]) -> None:
    _, _, app_resource = ids(values)
    auth = azure.arm.get(f"{app_resource}/authConfigs/current?api-version={CONTAINER_APPS_API}")
    properties = azure.check(auth, "read Container Apps authentication").get("properties", {}) if auth.status_code != 404 else {}
    enabled = properties.get("platform", {}).get("enabled")
    allowed = (properties.get("identityProviders", {}).get("azureActiveDirectory", {}).get("validation", {})
               .get("defaultAuthorizationPolicy", {}).get("allowedApplications"))
    connection = azure.check(azure.arm.get(
        f"{values['FOUNDRY_PROJECT_ID']}/connections/{CONNECTION}?api-version={CONNECTIONS_API}"), "read the connection")
    print(f"Container Apps authentication: {'on' if enabled else 'off'}")
    if enabled:
        print(f"  allowed client applications: {allowed if allowed is not None else 'not configured'}")
    print(f"Toolbox connection auth: {connection.get('properties', {}).get('authType')}")
    app = find_app(azure)
    if app:
        principals = azure.check(azure.graph.get("/servicePrincipals", params={"$filter": f"appId eq '{app['appId']}'"}),
                                 "look up the service principal")["value"]
        if principals:
            holders = [a.get("principalDisplayName", a["principalId"]) for a in assignments(azure, principals[0]["id"])]
            print(f"{ROLE} holders: {', '.join(holders) or 'none'}")
        else:
            print("MCP service principal: not found")
    print(f"Endpoint without a token: {probe(values['NETWORK_MCP_URL'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Secure the shared network MCP server with Microsoft Entra ID.")
    parser.add_argument("action", choices=["enable", "grant-agent", "revoke-agent", "set-server-role", "status", "disable"])
    parser.add_argument("--roles", action="store_true",
                        help=f"Pattern B: require the {ROLE} app role (checked by the server) instead of one allowed app.")
    parser.add_argument("--principal-id", help="Object ID of an agent identity (grant-agent, revoke-agent).")
    parser.add_argument("--no-connection", action="store_true",
                        help=f"grant-agent: only grant the role, do not create {AGENT_CONNECTION}.")
    parser.add_argument("--keep-allowed-application", action="store_true",
                        help="revoke-agent: keep the client allowed by Container Apps to isolate the server role check.")
    parser.add_argument("--required-role",
                        help="set-server-role: app role required by the server, e.g. Network.Read.")
    parser.add_argument("--token-version", type=int, choices=[1, 2], default=1,
                        help="Access token version for the MCP app (default 1, the managed-identity default).")
    args = parser.parse_args()
    if args.action in ("grant-agent", "revoke-agent") and not args.principal_id:
        parser.error(f"{args.action} needs --principal-id <agent identity object ID>")
    if args.action == "set-server-role" and not args.required_role:
        parser.error("set-server-role needs --required-role <app role>")
    values = security_values()
    if not values.get("NETWORK_MCP_URL") or "azurecontainerapps.io" not in values["NETWORK_MCP_URL"]:
        sys.exit("This environment has no lab-hosted network MCP server on Container Apps (NETWORK_MCP_URL).")
    azure = Azure()
    actions = {
        "enable": lambda: enable(azure, values, args.token_version, args.roles),
        "grant-agent": lambda: grant_agent(azure, values, args.principal_id, not args.no_connection),
        "revoke-agent": lambda: revoke_agent(
            azure, values, args.principal_id, keep_allowed_application=args.keep_allowed_application),
        "set-server-role": lambda: set_server_role(azure, values, args.required_role),
        "status": lambda: status(azure, values),
        "disable": lambda: disable(azure, values),
    }
    actions[args.action]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
