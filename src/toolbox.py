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
from azure.identity import AzureCliCredential

from lab_agent import load_settings, toolbox_settings

NETWORK_CONNECTION = "supportbuddy-network-mcp"
KNOWLEDGE_CONNECTION = "supportbuddy-runbooks-kb"
GUARDRAIL_ID = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/raiPolicies/[^/]+$",
    re.IGNORECASE,
)


def toolbox_tools(project: AIProjectClient) -> list[MCPToolboxTool]:
    """The tools in the toolbox. server_label becomes the prefix of each tool name."""
    network = project.connections.get(NETWORK_CONNECTION)
    knowledge = project.connections.get(KNOWLEDGE_CONNECTION)
    return [
        MCPToolboxTool(
            server_label="network",
            server_url=network.target,
            project_connection_id=NETWORK_CONNECTION,
            require_approval="never",  # read-only diagnostics
        ),
        MCPToolboxTool(
            server_label="runbooks",
            server_url=knowledge.target,
            project_connection_id=KNOWLEDGE_CONNECTION,
            require_approval="never",
            allowed_tools=["knowledge_base_retrieve"],
        ),
    ]


def create(project: AIProjectClient, name: str, guardrail: str | None) -> None:
    if guardrail and not GUARDRAIL_ID.match(guardrail):
        sys.exit("--guardrail needs the full resource ID: /subscriptions/.../accounts/<account>/raiPolicies/<name>")
    version = project.toolboxes.create_version(
        name=name,
        description="SupportBuddy lab tools: synthetic network diagnostics and runbooks (training data only).",
        tools=toolbox_tools(project),
        policies=ToolboxPolicies(rai_config=RaiConfig(rai_policy_name=guardrail)) if guardrail else None,
    )
    # A new version is not served until it is the default: promote it explicitly.
    project.toolboxes.update(name, default_version=version.version)
    print(f"Toolbox '{name}' version {version.version} is now the default.")
    if guardrail:
        print("The guardrail now screens tool inputs and outputs through this toolbox.")


async def show(endpoint: str) -> None:
    from agent_framework.foundry import FoundryToolbox
    from azure.identity.aio import AzureCliCredential as AsyncAzureCliCredential

    async with AsyncAzureCliCredential() as credential, FoundryToolbox(credential, url=endpoint) as toolbox:
        print(f"Tools served at {endpoint}:")
        for function in toolbox.functions:
            print(f"  - {function.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage your SupportBuddy Foundry Toolbox.")
    parser.add_argument("action", choices=["create", "show"])
    parser.add_argument("--guardrail", help="Stage 13: resource ID of a guardrail (RAI policy) to apply to the tools.")
    args = parser.parse_args()

    settings = load_settings()
    toolbox = toolbox_settings()
    if args.action == "show":
        asyncio.run(show(toolbox["TOOLBOX_ENDPOINT"]))
        return 0
    with AIProjectClient(endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=AzureCliCredential()) as project:
        create(project, toolbox["TOOLBOX_NAME"], args.guardrail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
