"""Finish the SupportBuddy lab environment after `azd provision` (runs as the azd postprovision hook).

Bicep creates the Azure resources; this script creates what lives *inside* them:
  1. the toolbox connections (Stage 9) and the shared Application Insights connection (Stage 10);
  2. the Foundry IQ knowledge base with the 12 synthetic runbooks (Stage 8);
  3. the Foundry Memory store (Stage 11);
and prints the values to give the participants.

Run it again at any time from the instructor folder (it is idempotent):
    uv run --project ../src python setup_lab.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from azure.identity import AzureCliCredential

SRC = Path(__file__).resolve().parents[1] / "src"
ARM = "https://management.azure.com"
CONNECTIONS_API = "2025-06-01"
INSIGHTS_API = "2020-02-02"
KB_API = "2026-08-01-preview"
REQUIRED = [
    "AZURE_AI_PROJECT_ENDPOINT", "AZURE_AI_MODEL_DEPLOYMENT_NAME", "AZURE_AI_EMBEDDING_DEPLOYMENT_NAME",
    "AZURE_AI_SEARCH_ENDPOINT", "FOUNDRY_PROJECT_ID", "SUPPORTBUDDY_GUARDRAIL_ID",
    "APPLICATIONINSIGHTS_RESOURCE_ID",
]


def azd_values() -> dict[str, str]:
    """Values from the environment (azd hooks) or, when run by hand, from `azd env get-values`."""
    values = {name: os.environ[name] for name in [*REQUIRED, "NETWORK_MCP_URL"] if os.getenv(name)}
    if any(name not in values for name in REQUIRED):
        output = subprocess.run(["azd", "env", "get-values", "--output", "json"], capture_output=True, text=True,
                                check=True, shell=sys.platform == "win32").stdout
        values = {**json.loads(output), **values}
    missing = [name for name in REQUIRED if not values.get(name)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}. Run `azd provision` first, from the instructor folder.")
    return values


def create_connections(values: dict[str, str]) -> None:
    token = AzureCliCredential().get_token(f"{ARM}/.default").token
    insights_id = values["APPLICATIONINSIGHTS_RESOURCE_ID"]
    response = httpx.get(
        f"{ARM}{insights_id}?api-version={INSIGHTS_API}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    connection_string = response.json().get("properties", {}).get("ConnectionString")
    if not isinstance(connection_string, str) or not connection_string.strip():
        raise ValueError("Application Insights did not return a connection string; project tracing cannot be configured.")
    search = values["AZURE_AI_SEARCH_ENDPOINT"].rstrip("/")
    connections = {
        # The project SDK expects ApiKey credentials for this connection, even with Entra-authenticated ingestion.
        "supportbuddy-appinsights": {
            "category": "AppInsights",
            "authType": "ApiKey",
            "target": insights_id,
            "credentials": {"key": connection_string},
            "metadata": {"ResourceId": insights_id},
        },
        # The knowledge base, called with the project's managed identity.
        "supportbuddy-runbooks-kb": {
            "category": "RemoteTool",
            "authType": "ProjectManagedIdentity",
            "audience": "https://search.azure.com/",
            "target": f"{search}/knowledgebases/supportbuddy-runbooks/mcp?api-version={KB_API}",
        },
    }
    if values.get("NETWORK_MCP_URL"):
        # The shared network MCP server: fictional read-only data, no authentication.
        connections["supportbuddy-network-mcp"] = {
            "category": "RemoteTool", "authType": "None", "target": values["NETWORK_MCP_URL"],
        }
    for name, properties in connections.items():
        url = f"{ARM}{values['FOUNDRY_PROJECT_ID']}/connections/{name}?api-version={CONNECTIONS_API}"
        response = httpx.put(url, json={"properties": properties}, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        response.raise_for_status()
        print(f"  connection {name}: ok")


def run_lab_script(args: list[str], env: dict[str, str], what: str) -> None:
    """Run a lab script from src; retry while new role assignments propagate (it can take a few minutes)."""
    for attempt in range(1, 13):
        result = subprocess.run([sys.executable, *args], cwd=SRC, env=env, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  {what}: ok")
            return
        if attempt == 1:
            print(f"  {what}: waiting for role assignments to propagate...")
        time.sleep(30)
    sys.exit(f"{what} failed:\n{result.stdout}\n{result.stderr}")


def main() -> int:
    values = azd_values()
    env = {
        **os.environ,
        "AZURE_AI_PROJECT_ENDPOINT": values["AZURE_AI_PROJECT_ENDPOINT"],
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": values["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        "AZURE_AI_EMBEDDING_DEPLOYMENT_NAME": values["AZURE_AI_EMBEDDING_DEPLOYMENT_NAME"],
        "AZURE_AI_SEARCH_ENDPOINT": values["AZURE_AI_SEARCH_ENDPOINT"],
        # Set the shared names here so a src/.env left over from another environment cannot override them.
        "FOUNDRY_KNOWLEDGE_BASE_NAME": "supportbuddy-runbooks",
        "FOUNDRY_MEMORY_STORE_NAME": "supportbuddy-memory",
        # The lab scripts need a participant prefix; the shared resources do not use it.
        "WORKSHOP_RESOURCE_PREFIX": os.getenv("WORKSHOP_RESOURCE_PREFIX", "lab-setup"),
        "PYTHONIOENCODING": "utf-8",
    }

    print("SupportBuddy lab: finishing the setup")
    create_connections(values)
    run_lab_script(["knowledge_base.py", "create"], env, "knowledge base supportbuddy-runbooks")
    run_lab_script(["memory_store.py", "create"], env, "memory store supportbuddy-memory")

    print(
        "\nGive the participants these lines for src/.env (each adds their own WORKSHOP_RESOURCE_PREFIX):\n\n"
        f"AZURE_AI_PROJECT_ENDPOINT={values['AZURE_AI_PROJECT_ENDPOINT']}\n"
        f"AZURE_AI_MODEL_DEPLOYMENT_NAME={values['AZURE_AI_MODEL_DEPLOYMENT_NAME']}\n"
        "MOCK_MCP_URL=http://127.0.0.1:8000/mcp\n"
        f"AZURE_AI_SEARCH_ENDPOINT={values['AZURE_AI_SEARCH_ENDPOINT']}\n"
        "FOUNDRY_KNOWLEDGE_BASE_NAME=supportbuddy-runbooks\n"
        f"AZURE_AI_EMBEDDING_DEPLOYMENT_NAME={values['AZURE_AI_EMBEDDING_DEPLOYMENT_NAME']}\n"
        "FOUNDRY_MEMORY_STORE_NAME=supportbuddy-memory\n"
        "SUPPORTBUDDY_TRACING=true\n"
        "SUPPORTBUDDY_TRACE_CONTENT=false\n"
        + ("" if values.get("NETWORK_MCP_URL") else "NETWORK_TOOLS=local\n")
        + f"\nStage 13 guardrail ID:\n{values['SUPPORTBUDDY_GUARDRAIL_ID']}\n"
        f"\nStage 14 project resource ID (azd ai agent init --project-id):\n{values['FOUNDRY_PROJECT_ID']}\n"
        f"\nShared network MCP server (Stage 9): {values.get('NETWORK_MCP_URL') or 'none, network tools stay local'}\n"
        f"\nShared Application Insights (Stage 10): {values['APPLICATIONINSIGHTS_RESOURCE_ID']}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
