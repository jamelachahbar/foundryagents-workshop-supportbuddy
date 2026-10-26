"""Stage 7 - Answer, act or hand over (human in the loop).

Sufficient evidence -> evidence-grounded answer.
A state-changing action (remote modem restart) -> runs only after a person approves it.
Insufficient or conflicting evidence -> structured handover to a human advisor, who works the
case on the operator desk and replies; the agent relays the reply with get_handover_status.

Start the MCP server and the operator desk first, each in its own terminal, from the src folder:
    uv run python -m mcp_server.server
    uv run python -m operator_desk
Then run:
    uv run python -m solutions.stage_7_answer_or_handoff ["prompt"]
"""

import os

from agent_framework import MCPStreamableHTTPTool

from lab_agent import build_agent, load_settings, run
from tools.support_tools import escalate_to_human, get_handover_status, restart_modem

DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"


def tools() -> list:
    load_settings()
    return [
        MCPStreamableHTTPTool(
            name="post_network",
            url=os.getenv("POST_MOCK_MCP_URL", DEFAULT_MCP_URL),
            approval_mode="never_require",
            load_prompts=False,
        ),
        escalate_to_human,
        get_handover_status,
        restart_modem,
    ]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
