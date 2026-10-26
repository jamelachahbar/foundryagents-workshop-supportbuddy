"""Stage 14 - SupportBuddy as a Foundry hosted agent.

Same Agent Framework code as Stages 9 and 12, served over the Responses protocol by
ResponsesHostServer. Locally it listens on http://localhost:8088. Deployed with
`azd deploy`, Foundry runs this code for you: it builds it, gives it a managed identity
and an endpoint, scales it, and shows it in the portal next to your prompt agent.

What changes compared to the local agent:
  - credentials: DefaultAzureCredential (your az login locally, the agent's identity in Foundry);
  - settings: Foundry injects FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME;
  - no DevUI and no console: callers use the Responses API (portal playground, azd ai agent invoke).
What does not change: the instructions, the toolbox and the local escalate_to_human function.

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
        # Local Python functions: possible here, impossible in a prompt agent.
        escalate_to_human,
        get_handover_status,
        restart_modem,
    ]


def create_agent():
    return build_agent(__file__, tools(), credential=DefaultAzureCredential())


if __name__ == "__main__":
    ResponsesHostServer(create_agent()).run()
