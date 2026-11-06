"""Turn Microsoft Entra authentication on or off for the shared network MCP server (Stage 9 security demo).

One command, run from the instructor folder after `azd up`:

    uv run --project ../src python secure_mcp.py enable     # require Entra; only the Foundry project may call it
    uv run --project ../src python secure_mcp.py status     # show what is configured and test the endpoint
    uv run --project ../src python secure_mcp.py disable    # back to the lab default (no authentication)

`enable` does, in order:
  1. calls the MCP endpoint without a token (expect 200: the open door);
  2. finds the Foundry project's managed identity;
  3. creates or reuses the app registration `supportbuddy-network-mcp` (ID URI api://<app-id>);
  4. turns on Container Apps built-in authentication: 401 without a valid token, and only the
     project's managed identity is an allowed client application;
  5. switches the toolbox connection `supportbuddy-network-mcp` to the project managed identity;
  6. calls the endpoint without a token again until it returns 401;
  7. if src/.env has a participant prefix, lists that participant's toolbox tools to show the
     toolbox still reaches the server.

Needs: Owner or Contributor on the resource group, and the right to create app registrations in Entra ID.
Lab construct: the server only serves synthetic, read-only data. Re-running `azd provision` (or setup_lab.py)
resets the toolbox connection to no authentication: run `secure_mcp.py enable` again afterwards.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

import httpx
from azure.identity import AzureCliCredential

from setup_lab import ARM, CONNECTIONS_API, SRC, azd_values

GRAPH = "https://graph.microsoft.com/v1.0"
CONTAINER_APPS_API = "2024-03-01"
SUBSCRIPTIONS_API = "2022-12-01"
APP_NAME = "supportbuddy-network-mcp"
CONTAINER_APP = "supportbuddy-network-mcp"
CONNECTION = "supportbuddy-network-mcp"
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "secure_mcp", "version": "1"}},
}


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


def project_identity_client_id(azure: Azure, project_id: str) -> str:
    project = azure.check(azure.arm.get(f"{project_id}?api-version={CONNECTIONS_API}"), "read the Foundry project")
    principal_id = (project.get("identity") or {}).get("principalId")
    if not principal_id:
        sys.exit("The Foundry project has no system-assigned managed identity.")
    service_principal = azure.check(azure.graph.get(f"/servicePrincipals/{principal_id}"),
                                    "read the project's managed identity")
    return service_principal["appId"]


def ensure_app_registration(azure: Azure, token_version: int) -> str:
    found = azure.check(azure.graph.get("/applications", params={"$filter": f"displayName eq '{APP_NAME}'"}),
                        "look up the app registration")["value"]
    if found:
        app = found[0]
        print(f"  app registration {APP_NAME}: reused ({app['appId']})")
    else:
        app = azure.check(azure.graph.post("/applications", json={
            "displayName": APP_NAME, "signInAudience": "AzureADMyOrg"}), "create the app registration")
        print(f"  app registration {APP_NAME}: created ({app['appId']})")
    azure.check(azure.graph.patch(f"/applications/{app['id']}", json={
        "identifierUris": [f"api://{app['appId']}"],
        "api": {"requestedAccessTokenVersion": token_version if token_version == 2 else None},
    }), "set the app ID URI")
    existing = azure.check(azure.graph.get("/servicePrincipals", params={"$filter": f"appId eq '{app['appId']}'"}),
                           "look up the service principal")["value"]
    if not existing:
        azure.check(azure.graph.post("/servicePrincipals", json={"appId": app["appId"]}), "create the service principal")
    return app["appId"]


def set_connection(azure: Azure, values: dict[str, str], audience: str | None) -> None:
    properties = {"category": "RemoteTool", "target": values["NETWORK_MCP_URL"],
                  **({"authType": "ProjectManagedIdentity", "audience": audience} if audience else {"authType": "None"})}
    azure.check(azure.arm.put(f"{values['FOUNDRY_PROJECT_ID']}/connections/{CONNECTION}?api-version={CONNECTIONS_API}",
                              json={"properties": properties}), "update the toolbox connection")
    print(f"  connection {CONNECTION}: {properties['authType']}" + (f" (audience {audience})" if audience else ""))


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
              "if it still fails, retry `enable --token-version 2`.")


def enable(azure: Azure, values: dict[str, str], token_version: int) -> None:
    subscription, _, app_resource = ids(values)
    url = values["NETWORK_MCP_URL"]
    print(f"1. Without a token: {probe(url)} (expect 200 before securing)")
    print("2. Foundry project identity")
    client_id = project_identity_client_id(azure, values["FOUNDRY_PROJECT_ID"])
    print(f"  project managed identity client ID: {client_id}")
    print("3. App registration for the MCP server")
    app_id = ensure_app_registration(azure, token_version)
    tenant = azure.check(azure.arm.get(f"/subscriptions/{subscription}?api-version={SUBSCRIPTIONS_API}"),
                         "read the subscription")["tenantId"]
    issuer = f"https://login.microsoftonline.com/{tenant}/v2.0" if token_version == 2 else f"https://sts.windows.net/{tenant}/"
    print("4. Container Apps authentication")
    azure.check(azure.arm.put(f"{app_resource}/authConfigs/current?api-version={CONTAINER_APPS_API}", json={"properties": {
        "platform": {"enabled": True},
        "globalValidation": {"unauthenticatedClientAction": "Return401"},
        "identityProviders": {"azureActiveDirectory": {
            "enabled": True,
            "registration": {"clientId": app_id, "openIdIssuer": issuer},
            "validation": {
                "allowedAudiences": [f"api://{app_id}", app_id],
                "defaultAuthorizationPolicy": {"allowedApplications": [client_id]},
            },
        }},
    }}), "turn on Container Apps authentication")
    print(f"  required: Entra token, audience api://{app_id}, caller = project identity only (issuer {issuer})")
    print("5. Toolbox connection")
    set_connection(azure, values, f"api://{app_id}")
    print(f"6. Without a token: {wait_for(url, 401)} (expect 401 now; changes can take a minute)")
    print("7. Through the toolbox")
    list_toolbox_tools()


def disable(azure: Azure, values: dict[str, str]) -> None:
    _, _, app_resource = ids(values)
    print("1. Container Apps authentication: off")
    response = azure.arm.delete(f"{app_resource}/authConfigs/current?api-version={CONTAINER_APPS_API}")
    if response.status_code not in (200, 202, 204, 404):
        azure.check(response, "turn off Container Apps authentication")
    print("2. Toolbox connection")
    set_connection(azure, values, None)
    print(f"3. Without a token: {wait_for(values['NETWORK_MCP_URL'], 200)} (expect 200: lab default)")
    print(f"The app registration {APP_NAME} is kept for next time; delete it in Entra ID if you no longer need it.")


def status(azure: Azure, values: dict[str, str]) -> None:
    _, _, app_resource = ids(values)
    auth = azure.arm.get(f"{app_resource}/authConfigs/current?api-version={CONTAINER_APPS_API}")
    enabled = auth.status_code == 200 and auth.json().get("properties", {}).get("platform", {}).get("enabled")
    connection = azure.check(azure.arm.get(
        f"{values['FOUNDRY_PROJECT_ID']}/connections/{CONNECTION}?api-version={CONNECTIONS_API}"), "read the connection")
    print(f"Container Apps authentication: {'on' if enabled else 'off'}")
    print(f"Toolbox connection auth: {connection.get('properties', {}).get('authType')}")
    print(f"Endpoint without a token: {probe(values['NETWORK_MCP_URL'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Secure the shared network MCP server with Microsoft Entra ID.")
    parser.add_argument("action", choices=["enable", "status", "disable"])
    parser.add_argument("--token-version", type=int, choices=[1, 2], default=1,
                        help="Access token version for the MCP app (default 1, the managed-identity default).")
    args = parser.parse_args()
    values = azd_values()
    if not values.get("NETWORK_MCP_URL") or "azurecontainerapps.io" not in values["NETWORK_MCP_URL"]:
        sys.exit("This environment has no lab-hosted network MCP server on Container Apps (NETWORK_MCP_URL).")
    azure = Azure()
    {"enable": lambda: enable(azure, values, args.token_version),
     "status": lambda: status(azure, values),
     "disable": lambda: disable(azure, values)}[args.action]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
