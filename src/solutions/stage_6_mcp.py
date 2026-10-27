"""Stage 6 - MCP.

Python function -> enterprise capability -> MCP server -> Foundry agent

The four diagnostic capabilities now live in a separate process (the synthetic
"network operations" MCP server). The agent discovers them over MCP; the tool
names, parameters and the instructions are the same as in Stage 5.

Start the MCP server first, in another terminal, from the src folder:
    uv run python -m mcp_server.server
Then run:
    uv run python -m solutions.stage_6_mcp ["prompt"]
"""

import os

from agent_framework import MCPStreamableHTTPTool

from lab_agent import build_agent, load_settings, run

DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"


def tools() -> list:
    load_settings()
    return [
        # Client-side MCP: this process calls the server, so a localhost URL works.
        MCPStreamableHTTPTool(
            name="network",
            url=os.getenv("MOCK_MCP_URL", DEFAULT_MCP_URL),
            approval_mode="never_require",
            load_prompts=False,
        ),
    ]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
