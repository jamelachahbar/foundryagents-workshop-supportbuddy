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
"""

from agent_framework.foundry import FoundryToolbox
from azure.identity.aio import AzureCliCredential

from lab_agent import build_agent, run, toolbox_settings
from tools.support_tools import escalate_to_human, get_handover_status, restart_modem


def tools() -> list:
    toolbox = toolbox_settings()
    return [
        # One MCP connection for every Foundry-managed tool. Token scope: https://ai.azure.com/.default
        FoundryToolbox(AzureCliCredential(), url=toolbox["TOOLBOX_ENDPOINT"]),
        escalate_to_human,
        get_handover_status,
        restart_modem,
    ]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
