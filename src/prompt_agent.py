"""Publish SupportBuddy as a Foundry prompt agent, and talk to it (Stage 10).

Run from the src folder:
    uv run python prompt_agent.py create                  # publish a new version of the prompt agent
    uv run python prompt_agent.py ask "I'm CUST-1003, my Wi-Fi is slow upstairs"
    uv run python prompt_agent.py chat                    # the same agent in DevUI
    uv run python -m contact_centre --prompt-agent        # customer chat + operator desk

A prompt agent is declarative: a definition (model + instructions + tools) stored in Foundry.
Foundry Agent Service runs the agent loop and calls the toolbox itself; no custom orchestration is deployed.
Agent Framework's FoundryAgent connects to it like to any other agent, so DevUI, the console and
the contact centre work with it unchanged.

Optional experiment: `create --with-functions` also declares your three Python functions
(escalate_to_human, get_handover_status, restart_modem) as function tools. Foundry knows their name
and parameters, not their Python bodies: the run returns each call to the application, and
FoundryAgent runs the function and sends the result back. The portal does not host these implementations.
Custom business logic can also be exposed as remote MCP or OpenAPI tools; handover does not require hosting
your own agent loop.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_framework.foundry import FoundryAgent
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, MCPTool, PromptAgentDefinition, Reasoning
from azure.identity import AzureCliCredential
from azure.identity.aio import AzureCliCredential as AsyncAzureCliCredential

from lab_agent import HideFinalTextReplay, agent_name, load_instructions, load_settings, run, toolbox_settings
from lab_telemetry import configure_tracing
from tools.support_tools import escalate_to_human, get_handover_status, restart_modem

INSTRUCTIONS = Path(__file__).parent / "solutions" / "stage_10_prompt_agent.instructions.md"
INSTRUCTIONS_WITH_FUNCTIONS = Path(__file__).parent / "solutions" / "stage_10_prompt_agent_functions.instructions.md"
# Your Python functions: declared in the definition, run by the application (never by Foundry).
LOCAL_FUNCTIONS = [escalate_to_human, get_handover_status, restart_modem]
FUNCTION_ELIGIBILITY = {
    "escalate_to_human": (
        "Use only for an explicit human request, failed identification after one retry, actual failed "
        "required diagnostics, a completed diagnosis requiring handover, or a problem persisting after "
        "applicable steps. Checks not attempted yet are NOT failed or unavailable checks: call the "
        "remaining network diagnostics instead. Successful identification alone does not justify handover. "
        "An identified customer reporting an outage needs line status, area incidents and equipment checks "
        "before a diagnostic handover decision. Record only tools actually attempted."
    ),
    "get_handover_status": (
        "Use only when the customer explicitly asks about an existing, confirmed HO-... reference. "
        "Do not call during initial diagnosis or invent a reference."
    ),
    "restart_modem": (
        "Use only after an explicit customer request or acceptance of a remote restart AND successful "
        "diagnostics establishing line up, no open area incident, unreachable modem, and an applicable "
        "retrieved restart procedure. A symptom report is not consent. Never restart during an area outage "
        "or with uncollected diagnostic evidence. Framework approval is additionally required."
    ),
}


def prompt_agent_name() -> str:
    return f"{agent_name()}-prompt"


def function_declaration(function) -> FunctionTool:  # noqa: ANN001
    """The name, description and JSON schema of a local function, without its code."""
    spec = function.to_json_schema_spec()["function"]
    description = f"{spec['description']}\n\nEligibility: {FUNCTION_ELIGIBILITY[spec['name']]}"
    return FunctionTool(name=spec["name"], description=description, parameters=spec["parameters"], strict=False)


def create(project: AIProjectClient, model: str, toolbox_endpoint: str, with_functions: bool = False) -> None:
    tools = [
        # The same toolbox as Stage 9, but now Foundry (not your laptop) connects to it.
        MCPTool(server_label="supportbuddy_tools", server_url=toolbox_endpoint, require_approval="never"),
    ]
    if with_functions:
        tools += [function_declaration(f) for f in LOCAL_FUNCTIONS]
    definition = PromptAgentDefinition(
        model=model,
        instructions=load_instructions(str(INSTRUCTIONS_WITH_FUNCTIONS if with_functions else INSTRUCTIONS)),
        reasoning=Reasoning(effort="medium"),
        tools=tools,
    )
    version = project.agents.create_version(agent_name=prompt_agent_name(), definition=definition)
    print(f"Prompt agent '{version.name}' version {version.version} published.")
    print("Open it in the Foundry portal: Build > Agents, then use the playground.")


def connect_prompt_agent(*, devui: bool = False, name: str | None = None) -> FoundryAgent:
    """Agent Framework's client for your prompt agent (latest version). It runs the functions the agent declares."""
    settings = load_settings()
    with AIProjectClient(endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=AzureCliCredential()) as project:
        configure_tracing(project)
        latest = project.agents.get(agent_name=name or prompt_agent_name()).versions.latest
    declared = {tool.get("name") for tool in latest.definition.get("tools", []) if tool.get("type") == "function"}
    return FoundryAgent(
        project_endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"],
        agent_name=latest.name,
        agent_version=latest.version,
        name=latest.name,
        id=f"{latest.name}:{latest.version}",
        credential=AsyncAzureCliCredential(),
        tools=[f for f in LOCAL_FUNCTIONS if f.name in declared] or None,
        default_options={"store": True},
        additional_properties={"evaluation_instructions": latest.definition.get("instructions")},
        middleware=[HideFinalTextReplay()] if devui else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SupportBuddy as a Foundry prompt agent.")
    parser.add_argument("action", choices=["create", "ask", "chat"])
    parser.add_argument("question", nargs="?")
    parser.add_argument("--with-functions", action="store_true", help="Experiment: also declare your Python functions.")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    if args.action == "create":
        with AIProjectClient(endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=AzureCliCredential()) as project:
            create(project, settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"], toolbox_settings()["TOOLBOX_ENDPOINT"], args.with_functions)
        return 0
    if args.action == "ask" and not args.question:
        parser.error('ask needs a question, for example "I\'m CUST-1003, my Wi-Fi is slow"')
    # lab_agent.run reads the prompt from the command line: DevUI without one, the console with one.
    sys.argv = [sys.argv[0], *([args.question] if args.action == "ask" else [])]
    run(connect_prompt_agent(devui=args.action == "chat"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
