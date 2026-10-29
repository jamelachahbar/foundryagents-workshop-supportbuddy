"""Stage 14 - SupportBuddy as a Foundry hosted agent.

Stage 9-style toolbox and support functions, served over the Responses protocol by
ResponsesHostServer. Locally it listens on http://localhost:8088. Deployed with
`azd deploy`, Foundry runs this code for you: it builds it, gives it a managed identity
and an endpoint, scales it, and shows it in the portal next to your prompt agent.

What changes compared to the local agent:
  - credentials: DefaultAzureCredential (your az login locally, the agent's identity in Foundry);
  - settings: Foundry injects FOUNDRY_PROJECT_ENDPOINT; deployment env supplies the model name;
  - no DevUI and no console: callers use the Responses API (portal playground, azd ai agent invoke).
This example has its own instructions and reuses the toolbox and local support functions.
It does not configure Stage 11 signed-in customer context/memory or Stage 13 customer-checking middleware.

Run locally from the src folder:   uv run python hosted_agent.py
Then, in another terminal:         azd ai agent invoke --local "I'm CUST-1004, my connection keeps dropping"
"""

import os

from agent_framework.foundry import FoundryToolbox
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

# In Foundry, the platform injects the project endpoint; locally it comes from src/.env.
if os.getenv("FOUNDRY_PROJECT_ENDPOINT"):
    os.environ.setdefault("AZURE_AI_PROJECT_ENDPOINT", os.environ["FOUNDRY_PROJECT_ENDPOINT"])

from lab_agent import build_agent, toolbox_settings  # noqa: E402
from tools.support_tools import escalate_to_human, get_handover_status, restart_modem  # noqa: E402


def tools() -> list:
    return [
        FoundryToolbox(AsyncDefaultAzureCredential(), url=toolbox_settings()["TOOLBOX_ENDPOINT"]),
        # Executed here; prompt-agent function tools execute in the calling application.
        escalate_to_human,
        get_handover_status,
        restart_modem,
    ]


def create_agent():
    return build_agent(__file__, tools(), credential=DefaultAzureCredential())


if __name__ == "__main__":
    ResponsesHostServer(create_agent()).run()
