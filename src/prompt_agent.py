"""Create and chat with SupportBuddy as a Foundry prompt agent (Stage 13).

Run from the src folder:
    uv run python prompt_agent.py create                  # publish a new version of the prompt agent
    uv run python prompt_agent.py ask "I'm CUST-1003, my Wi-Fi is slow upstairs"

A prompt agent is declarative: a definition (model + instructions + tools) stored in Foundry.
There is no code of yours running. When someone talks to it, Foundry Agent Service runs the
agent loop and calls the tools itself. That has consequences you will see in this stage:
  - only tools Foundry can call itself work (MCP, toolbox, knowledge, web search, ...);
  - your local Python functions (escalate_to_human) and middleware cannot be used;
  - everything is visible and editable in the Foundry portal (Build > Agents).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.identity import AzureCliCredential

from lab_agent import agent_name, load_settings, toolbox_settings

INSTRUCTIONS = Path(__file__).parent / "solutions" / "stage_13_prompt_agent.instructions.md"


def prompt_agent_name() -> str:
    return f"{agent_name()}-prompt"


def create(project: AIProjectClient, model: str, toolbox_endpoint: str) -> None:
    definition = PromptAgentDefinition(
        model=model,
        instructions=INSTRUCTIONS.read_text(encoding="utf-8"),
        tools=[
            # The same toolbox as Stage 9, but now Foundry (not your laptop) connects to it.
            MCPTool(
                server_label="supportbuddy_tools",
                server_url=toolbox_endpoint,
                require_approval="never",
            )
        ],
    )
    version = project.agents.create_version(agent_name=prompt_agent_name(), definition=definition)
    print(f"Prompt agent '{version.name}' version {version.version} published.")
    print("Open it in the Foundry portal: Build > Agents, then use the playground.")


def ask(project: AIProjectClient, question: str) -> None:
    openai = project.get_openai_client()
    response = openai.responses.create(
        input=question,
        extra_body={"agent_reference": {"name": prompt_agent_name(), "type": "agent_reference"}},
    )
    for item in response.output:
        if item.type == "mcp_call":
            print(f"  [tool call, run by Foundry] {item.name}")
    print(f"\nAgent: {response.output_text}")


def main() -> int:
    parser = argparse.ArgumentParser(description="SupportBuddy as a Foundry prompt agent.")
    parser.add_argument("action", choices=["create", "ask"])
    parser.add_argument("question", nargs="?")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    with AIProjectClient(endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=AzureCliCredential()) as project:
        if args.action == "create":
            create(project, settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"], toolbox_settings()["TOOLBOX_ENDPOINT"])
        else:
            if not args.question:
                parser.error('ask needs a question, for example "I\'m CUST-1003, my Wi-Fi is slow"')
            ask(project, args.question)
    return 0


if __name__ == "__main__":
    sys.exit(main())
