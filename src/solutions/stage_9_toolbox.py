"""Stage 9 - Foundry Toolbox.

In Stage 8 the agent held two MCP connections, and the code knew each URL and how to
authenticate to each one. Now the network diagnostics and the runbooks are bundled in a
Foundry Toolbox: one versioned MCP endpoint in your Foundry project. Foundry calls the
underlying tools with the credentials stored in project connections, so the agent code
only needs the toolbox endpoint and your Entra identity.

The human-in-the-loop tools stay local Python functions: they are part of this agent's own code.

One-time setup (from the src folder):  uv run python toolbox.py create
Then run (no local MCP server needed any more):
    uv run python -m solutions.stage_9_toolbox ["prompt"]
    uv run python -m contact_centre solutions.stage_9_toolbox

With NETWORK_TOOLS=local in .env (no network MCP server on Azure), the toolbox holds only the
runbooks and the agent still connects to the network MCP server on your laptop, as in Stage 6.
"""

import os

from agent_framework import MCPStreamableHTTPTool
from agent_framework.foundry import FoundryToolbox
from azure.identity.aio import AzureCliCredential

from lab_agent import build_agent, run, toolbox_settings
from tools.support_tools import escalate_to_human, get_handover_status, restart_modem


def tools() -> list:
    """Build the agent's tool list for the normal Azure setup or the local fallback.

    ``toolbox_settings()`` returns connection settings, not the tools themselves.
    ``FoundryToolbox`` uses those settings and your Azure CLI identity to connect
    to one endpoint that exposes multiple remote functions.

    Normally, the toolbox provides both network diagnostics and runbook retrieval.
    The three support functions run locally in the agent in either setup.

    With ``NETWORK_TOOLS=local``, the toolbox provides only runbook retrieval.
    Append a separate connection to the local network MCP server for diagnostics.
    Return the assembled list for ``build_agent`` to register with the agent.
    """
    toolbox = toolbox_settings()
    tools = [
        # One MCP connection for every Foundry-managed tool. Token scope: https://ai.azure.com/.default
        FoundryToolbox(AzureCliCredential(), url=toolbox["TOOLBOX_ENDPOINT"]),
        escalate_to_human,
        get_handover_status,
        restart_modem,
    ]
    if toolbox["NETWORK_TOOLS"] == "local":
        tools.append(MCPStreamableHTTPTool(
            name="network",
            url=os.getenv("MOCK_MCP_URL", "http://127.0.0.1:8000/mcp"),
            approval_mode="never_require",
            load_prompts=False,
        ))
    return tools


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
