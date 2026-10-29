"""Stage 8 - Knowledge (Foundry IQ).

Tools give the agent evidence about the customer's situation; the knowledge base gives it the
procedure to follow. Troubleshooting steps now come from synthetic runbooks retrieved from a
Foundry IQ knowledge base, and every step is cited as [RB-xx Title].

A Foundry IQ knowledge base is itself an MCP server, so the agent gets a second MCP tool,
this time authenticated with Microsoft Entra ID.

Instructor setup once per shared environment (or your own personal environment), from src:
    uv run python knowledge_base.py create
Shared-workshop participants only verify access:
    uv run python knowledge_base.py ask "wifi slow upstairs, cable works"
Start the MCP server in another terminal: uv run python -m mcp_server.server
Then run:
    uv run python -m solutions.stage_8_knowledge ["prompt"]
"""

import os

from agent_framework import MCPStreamableHTTPTool

from lab_agent import build_agent, knowledge_settings, load_settings, run
from tools.knowledge_tools import runbook_knowledge_base
from tools.support_tools import escalate_to_human, get_handover_status, restart_modem

DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"


def tools() -> list:
    load_settings()
    knowledge_config = knowledge_settings()
    return [
        MCPStreamableHTTPTool(
            name="network",
            url=os.getenv("MOCK_MCP_URL", DEFAULT_MCP_URL),
            approval_mode="never_require",
            load_prompts=False,
        ),
        runbook_knowledge_base(
            search_endpoint=knowledge_config["AZURE_AI_SEARCH_ENDPOINT"],
            knowledge_base=knowledge_config["FOUNDRY_KNOWLEDGE_BASE_NAME"],
        ),
        escalate_to_human,
        get_handover_status,
        restart_modem,
    ]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
