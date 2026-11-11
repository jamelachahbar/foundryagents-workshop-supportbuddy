"""Create and inspect your Foundry Toolbox (Stage 9).

Run from the src folder:
    uv run python toolbox.py create                       # new toolbox version, made the default
    uv run python toolbox.py create --guardrail <policy-id>  # Stage 13: same tools, with a guardrail
    uv run python toolbox.py show                         # list the tools an agent will see

A toolbox is a named, versioned bundle of tools that lives in your Foundry project and is
served as ONE MCP endpoint. Your agent connects to that endpoint; which tools are behind it,
and how they authenticate, is managed in Foundry and can change without touching agent code.

The two tools in this toolbox use project connections that the instructor created once:
  - supportbuddy-network-mcp  the synthetic network-operations MCP server (Stage 6), hosted on Azure
  - supportbuddy-runbooks-kb  the Foundry IQ knowledge base (Stage 8), via the project's managed identity
Connections hold the target URL and the credentials, so the toolbox definition never contains secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPToolboxTool, RaiConfig, ToolboxPolicies
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureCliCredential

from lab_agent import load_settings, toolbox_settings

NETWORK_CONNECTION = "supportbuddy-network-mcp"
KNOWLEDGE_CONNECTION = "supportbuddy-runbooks-kb"
GUARDRAIL_ID = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/raiPolicies/[^/]+$",
    re.IGNORECASE,
)


def toolbox_tools(
    project: AIProjectClient, include_network: bool = True, network_connection: str = NETWORK_CONNECTION
) -> list[MCPToolboxTool]:
    """The tools in the toolbox. server_label becomes the prefix of each tool name."""
    knowledge = project.connections.get(KNOWLEDGE_CONNECTION)
    tools = [
        MCPToolboxTool(
            server_label="runbooks",
            server_url=knowledge.target,
            project_connection_id=KNOWLEDGE_CONNECTION,
            require_approval="never",
            allowed_tools=["knowledge_base_retrieve"],
        ),
    ]
    if include_network:
        try:
            network = project.connections.get(network_connection)
        except ResourceNotFoundError:
            sys.exit(f"The project has no '{network_connection}' connection. Without a network MCP server on Azure, "
                     "set NETWORK_TOOLS=local in src/.env and run this again.")
        tools.insert(0, MCPToolboxTool(
            server_label="network",
            server_url=network.target,
            project_connection_id=network_connection,
            require_approval="never",  # read-only diagnostics
        ))
    return tools


def create(
    project: AIProjectClient, name: str, guardrail: str | None, include_network: bool = True,
    network_connection: str = NETWORK_CONNECTION,
) -> None:
    if guardrail and not GUARDRAIL_ID.match(guardrail):
        sys.exit("--guardrail needs the full resource ID: /subscriptions/.../accounts/<account>/raiPolicies/<name>")
    version = project.toolboxes.create_version(
        name=name,
        description="SupportBuddy lab tools: synthetic network diagnostics and runbooks (training data only).",
        tools=toolbox_tools(project, include_network, network_connection),
        policies=ToolboxPolicies(rai_config=RaiConfig(rai_policy_name=guardrail)) if guardrail else None,
    )
    # A new version is not served until it is the default: promote it explicitly.
    project.toolboxes.update(name, default_version=version.version)
    print(f"Toolbox '{name}' version {version.version} is now the default.")
    if not include_network:
        print("NETWORK_TOOLS=local: the toolbox holds the runbooks; the network tools stay on your laptop.")
    if guardrail:
        print("The guardrail now screens tool inputs and outputs through this toolbox.")


async def show(project_endpoint: str, name: str, endpoint: str) -> None:
    from agent_framework.exceptions import ToolExecutionException
    from agent_framework.foundry import FoundryToolbox
    from azure.identity.aio import AzureCliCredential as AsyncAzureCliCredential

    with AIProjectClient(endpoint=project_endpoint, credential=AzureCliCredential()) as project:
        toolbox = project.toolboxes.get(name).as_dict()
        default_version = toolbox.get("default_version")
        if not default_version:
            print(f"Toolbox '{name}' has no published default version.")
            return
        version = project.toolboxes.get_version(name, str(default_version)).as_dict()
        routes = version.get("tools", [])
        connections = {
            route["project_connection_id"]: project.connections.get(route["project_connection_id"]).as_dict()
            for route in routes
            if route.get("project_connection_id")
        }

    discovery_unavailable = False
    try:
        async with AsyncAzureCliCredential() as credential, FoundryToolbox(credential, url=endpoint) as toolbox:
            function_names = sorted(function.name for function in toolbox.functions)
    except ToolExecutionException as error:
        needs_agent_context = "requires AgentInstanceClientId and AgentBlueprintClientId" in str(error)
        has_agent_identity_route = any(
            connection.get("credentials", {}).get("type") == "AgenticIdentityToken"
            for connection in connections.values()
        )
        if not (needs_agent_context and has_agent_identity_route):
            raise
        function_names = []
        discovery_unavailable = True

    print("SupportBuddy Foundry Toolbox")
    print(f"  Name: {name}")
    print(f"  Active version: {default_version}")
    print(f"  Endpoint used by the agent: {endpoint}")
    print("  Foundry serves this endpoint as one MCP toolbox; each group below routes to its own server.")

    for route in routes:
        label = route.get("server_label", "tools")
        connection_id = route.get("project_connection_id", "not configured")
        connection = connections.get(connection_id, {})
        credential_type = connection.get("credentials", {}).get("type", "unknown")
        auth_description = {
            "AgenticIdentityToken": "hosted agent identity",
            "ProjectManagedIdentity": "Foundry project identity",
            "None": "no connection credential",
        }.get(credential_type, "connection credential")
        prefix = f"{label}___"
        discovered = [tool for tool in function_names if tool.startswith(prefix)]

        print(f"\n{label.upper()} MCP")
        print(f"  Server endpoint: {route.get('server_url', 'not listed')}")
        print(f"  Foundry connection: {connection_id}")
        print(f"  Connection uses: {auth_description} ({credential_type})")
        print("  Tools available through this group:")
        if discovered:
            for tool in discovered:
                print(f"    - {tool.removeprefix(prefix)}  [{tool}]")
        elif discovery_unavailable and credential_type == "AgenticIdentityToken":
            print("    - Tool listing requires the hosted-agent identity; verify by invoking the hosted agent")
        else:
            print("    - No tools discovered for this group")

    if discovery_unavailable:
        print("\nLocal discovery cannot supply the hosted-agent identity; use a hosted conversation to verify these tools.")
    else:
        print("\nDiscovery only: listing tools does not prove a tool call succeeded or passed authorization.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage your SupportBuddy Foundry Toolbox.")
    parser.add_argument("action", choices=["create", "show"])
    parser.add_argument("--guardrail", help="Stage 13: resource ID of a guardrail (RAI policy) to apply to the tools.")
    parser.add_argument("--network-connection", default=NETWORK_CONNECTION,
                        help="Project connection for network tools (defaults to the shared workshop connection).")
    args = parser.parse_args()

    settings = load_settings()
    toolbox = toolbox_settings()
    if args.action == "show":
        asyncio.run(show(settings["AZURE_AI_PROJECT_ENDPOINT"], toolbox["TOOLBOX_NAME"], toolbox["TOOLBOX_ENDPOINT"]))
        return 0
    with AIProjectClient(endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=AzureCliCredential()) as project:
         create(project, toolbox["TOOLBOX_NAME"], args.guardrail, include_network=toolbox["NETWORK_TOOLS"] == "toolbox",
             network_connection=args.network_connection)
    return 0


if __name__ == "__main__":
    sys.exit(main())
