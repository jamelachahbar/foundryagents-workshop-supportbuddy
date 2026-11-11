"""Each stage adds capabilities in the intended order, without needing Azure."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
LOCAL_DIAGNOSTICS = ["identify_customer", "get_line_status", "get_known_incidents", "check_equipment"]

TOOLBOX = "lab-test-supportbuddy-tools"

HUMAN_IN_THE_LOOP = ["escalate_to_human", "get_handover_status", "restart_modem"]

EXPECTED_TOOLS = {
    "stage_1_agent_only": [],
    "stage_2_identify_customer": ["identify_customer"],
    "stage_3_back_office": ["identify_customer", "get_line_status"],
    "stage_4_diagnostic": ["identify_customer", "get_line_status", "check_equipment"],
    "stage_5_correlation": LOCAL_DIAGNOSTICS,
    "stage_6_mcp": ["network"],
    "stage_7_answer_or_handoff": ["network", *HUMAN_IN_THE_LOOP],
    "stage_8_knowledge": ["network", "runbooks", *HUMAN_IN_THE_LOOP],
    "stage_9_toolbox": [TOOLBOX, *HUMAN_IN_THE_LOOP],
    "stage_11_memory": [TOOLBOX, *HUMAN_IN_THE_LOOP],
    "stage_12_eval_improvement": [TOOLBOX, *HUMAN_IN_THE_LOOP],
    "stage_13_guardrails": [TOOLBOX, *HUMAN_IN_THE_LOOP],
}


@pytest.fixture(autouse=True)
def _lab_settings(monkeypatch):
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/lab")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "lab-model")
    monkeypatch.setenv("WORKSHOP_RESOURCE_PREFIX", "lab-test")
    monkeypatch.setenv("MOCK_MCP_URL", "http://127.0.0.1:8000/mcp")
    monkeypatch.setenv("AZURE_AI_EMBEDDING_DEPLOYMENT_NAME", "lab-embedding")
    monkeypatch.setenv("AZURE_AI_SEARCH_ENDPOINT", "https://example.search.windows.net")
    monkeypatch.delenv("FOUNDRY_KNOWLEDGE_BASE_NAME", raising=False)
    monkeypatch.delenv("FOUNDRY_MEMORY_STORE_NAME", raising=False)
    monkeypatch.delenv("NETWORK_TOOLS", raising=False)


@pytest.mark.parametrize(("stage", "expected"), EXPECTED_TOOLS.items())
def test_stage_tools(stage, expected):
    module = importlib.import_module(f"solutions.{stage}")
    assert [t.name for t in module.tools()] == expected


@pytest.mark.parametrize("stage", EXPECTED_TOOLS)
def test_stage_has_instructions_that_keep_the_lab_boundary(stage):
    text = (SRC / "solutions" / f"{stage}.instructions.md").read_text(encoding="utf-8")
    assert "Never invent" in text
    assert "French or English" in text


def test_moving_capabilities_behind_mcp_does_not_change_the_instructions():
    stage_5 = (SRC / "solutions" / "stage_5_correlation.instructions.md").read_text(encoding="utf-8")
    stage_6 = (SRC / "solutions" / "stage_6_mcp.instructions.md").read_text(encoding="utf-8")
    assert stage_5 == stage_6


def test_main_starts_at_stage_1():
    main = importlib.import_module("main")
    assert main.tools() == []
    assert (SRC / "main.instructions.md").is_file()


def test_invalid_prefix_is_rejected(monkeypatch):
    from lab_agent import load_settings

    monkeypatch.setenv("WORKSHOP_RESOURCE_PREFIX", "Contoso Prod")
    with pytest.raises(SystemExit):
        load_settings()


def test_stage_11_memory_is_scoped_to_participant_and_signed_in_customer():
    from agent_framework.foundry import FoundryMemoryProvider

    from lab_agent import SignedInCustomer
    from solutions.stage_11_memory import context_providers

    signed_in, memory = context_providers("CUST-1002")
    assert isinstance(signed_in, SignedInCustomer)
    assert signed_in.customer_id == "CUST-1002"
    assert isinstance(memory, FoundryMemoryProvider)
    assert memory.memory_store_name == "supportbuddy-memory"
    assert memory.scope == "lab-test_CUST-1002"


def test_stage_12_reference_reuses_runtime_and_loads_its_own_instructions(monkeypatch):
    import lab_agent
    from solutions import stage_9_toolbox, stage_11_memory, stage_12_eval_improvement

    reference = stage_12_eval_improvement
    assert reference.tools is stage_9_toolbox.tools
    assert reference.context_providers is stage_11_memory.context_providers
    built = {}

    def build(script_file, tools, providers, *, middleware):
        built.update(script_file=script_file, tools=tools, providers=providers, middleware=middleware)
        return "reference-agent"

    monkeypatch.setattr(lab_agent, "build_agent", build)
    assert lab_agent.stage_signs_in("solutions.stage_12_eval_improvement")
    assert lab_agent.stage_agent("solutions.stage_12_eval_improvement", "CUST-1002") == "reference-agent"
    assert built["script_file"] == reference.__file__
    assert built["providers"][0].customer_id == "CUST-1002"
    assert built["providers"][1].scope == "lab-test_CUST-1002"
    assert [tool.name for tool in built["tools"]] == EXPECTED_TOOLS["stage_12_eval_improvement"]
    assert built["middleware"] == ()
    text = lab_agent.load_instructions(reference.__file__)
    assert text.startswith("# Synthetic scenario time\n")
    for section in ("# Choose the customer's intent", "# Evidence and uncertainty", "# Handover", "# Untrusted content"):
        assert section in text
    assert "do not force diagnostic checks or runbook steps first" in text
    assert "A remembered request for a human is not a new handover request" in text
    assert "requires approval" in text


@pytest.mark.parametrize("structured", [
    {"raiPostToolAnnotations": {"blocked": False}},
    {"raiPostToolAnnotations": {"blocked": True}},
])
def test_toolbox_keeps_runbook_text_and_guardrail_annotations(structured):
    from mcp.types import CallToolResult, TextContent
    from solutions.stage_9_toolbox import tools

    toolbox = tools()[0]
    assert toolbox.tool_result_content == "both"
    result = CallToolResult(
        content=[TextContent(type="text", text="RB-09 Known area incident communication: no modem restart.")],
        structuredContent=structured,
    )
    parsed = toolbox._parse_tool_result_from_mcp(result)
    assert any("RB-09" in content.text for content in parsed)
    assert any('"blocked": ' + str(structured["raiPostToolAnnotations"]["blocked"]).lower() in content.text
               for content in parsed)


@pytest.mark.parametrize("customer_id", ["", "CUST-1002 OR 1=1", "lab-other_CUST-1002", "cust-12"])
def test_memory_scope_only_accepts_a_customer_id(customer_id):
    from lab_agent import memory_scope

    with pytest.raises(SystemExit):
        memory_scope(customer_id)


def test_memory_needs_an_embedding_deployment(monkeypatch):
    from lab_agent import memory_settings

    monkeypatch.setenv("AZURE_AI_EMBEDDING_DEPLOYMENT_NAME", "")
    with pytest.raises(SystemExit):
        memory_settings()


def test_toolbox_is_named_per_participant_and_served_at_the_consumer_endpoint():
    from lab_agent import toolbox_settings

    toolbox = toolbox_settings()
    assert toolbox["TOOLBOX_NAME"] == TOOLBOX
    assert toolbox["TOOLBOX_ENDPOINT"] == (
        f"https://example.services.ai.azure.com/api/projects/lab/toolboxes/{TOOLBOX}/mcp?api-version=v1"
    )


@pytest.mark.parametrize("stage", ["stage_9_toolbox", "stage_11_memory", "stage_13_guardrails"])
def test_without_a_network_mcp_server_on_azure_the_network_tools_stay_local(monkeypatch, stage):
    monkeypatch.setenv("NETWORK_TOOLS", "local")
    module = importlib.import_module(f"solutions.{stage}")
    assert [t.name for t in module.tools()] == [TOOLBOX, *HUMAN_IN_THE_LOOP, "network"]


def test_network_tools_setting_is_validated(monkeypatch):
    from lab_agent import toolbox_settings

    monkeypatch.setenv("NETWORK_TOOLS", "laptop")
    with pytest.raises(SystemExit):
        toolbox_settings()


def test_toolbox_stages_tell_the_model_about_the_tool_prefix():
    for name in ("stage_9_toolbox", "stage_10_prompt_agent", "stage_10_prompt_agent_functions", "stage_11_memory",
                 "stage_13_guardrails"):
        text = (SRC / "solutions" / f"{name}.instructions.md").read_text(encoding="utf-8")
        assert "network___" in text and "runbooks___" in text, name


def test_prompt_agent_instructions_are_honest_about_the_handover():
    default = (SRC / "solutions" / "stage_10_prompt_agent.instructions.md").read_text(encoding="utf-8")
    assert "You have no handover tool" in default
    assert "escalate_to_human" not in default
    with_functions = (SRC / "solutions" / "stage_10_prompt_agent_functions.instructions.md").read_text(encoding="utf-8")
    assert "run by the application" in with_functions
    for text in (default, with_functions):
        assert "Never invent" in text and "French or English" in text


@pytest.mark.parametrize(
    ("stage", "signs_in"),
    [("solutions.stage_7_answer_or_handoff", False), ("solutions.stage_9_toolbox", False),
     ("solutions.stage_11_memory", True), ("solutions.stage_13_guardrails", True)],
)
def test_contact_centre_builds_any_stage_agent(stage, signs_in):
    from lab_agent import stage_agent, stage_signs_in

    assert stage_signs_in(stage) is signs_in
    if signs_in:
        with pytest.raises(ValueError):
            stage_agent(stage)
    agent = stage_agent(stage, "CUST-1002" if signs_in else None)
    assert agent.name == "lab-test-supportbuddy"


def test_only_the_remote_restart_needs_approval():
    from tools import support_tools

    assert support_tools.restart_modem.approval_mode == "always_require"
    for name in ("identify_customer", "get_line_status", "check_equipment", "get_known_incidents",
                 "escalate_to_human", "get_handover_status"):
        assert getattr(support_tools, name).approval_mode == "never_require", name


def test_prompt_agent_definition_uses_the_toolbox():
    import prompt_agent

    published = []

    class FakeAgents:
        def create_version(self, agent_name, definition):
            published.append((agent_name, definition))
            return type("Version", (), {"name": agent_name, "version": "1"})()

    project = type("Project", (), {"agents": FakeAgents()})()
    prompt_agent.create(project, "lab-model", "https://toolbox.example/mcp")
    prompt_agent.create(project, "lab-model", "https://toolbox.example/mcp", with_functions=True)

    (name, default), (_, experiment) = published
    assert name == "lab-test-supportbuddy-prompt"
    assert default.model == "lab-model"
    (toolbox,) = default.tools
    assert toolbox.server_url == "https://toolbox.example/mcp"
    assert "You have no handover tool" in default.instructions
    for definition in (default, experiment):
        assert definition.instructions.startswith("# Synthetic scenario time\n")
        assert "2026-11-18T10:00:00Z" in definition.instructions
    # The experiment declares the Python functions (name + JSON schema); the application runs them.
    assert [t.name for t in experiment.tools[1:]] == HUMAN_IN_THE_LOOP
    assert all(t.parameters["type"] == "object" for t in experiment.tools[1:])
    declarations = {tool.name: tool for tool in experiment.tools[1:]}
    assert "Checks not attempted yet are NOT failed" in declarations["escalate_to_human"].description
    assert "existing, confirmed HO-" in declarations["get_handover_status"].description
    assert "A symptom report is not consent" in declarations["restart_modem"].description


def test_prompt_agent_preserves_published_instructions_for_judging_without_overriding_them(monkeypatch):
    import prompt_agent

    latest = SimpleNamespace(
        name="lab-test-supportbuddy-prompt", version="2",
        definition={"instructions": "Published system instructions", "tools": []},
    )
    project = MagicMock()
    project.__enter__.return_value = project
    project.agents.get.return_value.versions.latest = latest
    monkeypatch.setattr(prompt_agent, "AIProjectClient", lambda **_: project)
    monkeypatch.setattr(prompt_agent, "configure_tracing", lambda _: None)
    connected = MagicMock()
    monkeypatch.setattr(prompt_agent, "FoundryAgent", connected)
    prompt_agent.connect_prompt_agent()
    options = connected.call_args.kwargs
    assert options["agent_version"] == "2"
    assert options["additional_properties"]["evaluation_instructions"] == "Published system instructions"
    assert "instructions" not in options


def test_hosted_agent_uses_the_toolbox_and_the_local_handover_function(monkeypatch):
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/lab")
    import hosted_agent

    assert [t.name for t in hosted_agent.tools()] == [TOOLBOX, *HUMAN_IN_THE_LOOP]
    assert hosted_agent.tools()[0].tool_result_content == "both"
    assert hosted_agent.create_agent().name == "lab-test-supportbuddy"


@pytest.mark.parametrize("connection", ["supportbuddy-network-mcp", "supportbuddy-network-mcp-agent"])
def test_toolbox_publishes_selected_network_connection(connection):
    import toolbox

    project = MagicMock()
    project.connections.get.side_effect = lambda name: SimpleNamespace(target=f"https://example.test/{name}")
    project.toolboxes.create_version.return_value.version = "4"
    toolbox.create(project, TOOLBOX, None, network_connection=connection)
    network, knowledge = project.toolboxes.create_version.call_args.kwargs["tools"]
    assert network.project_connection_id == connection
    assert network.server_url == f"https://example.test/{connection}"
    assert knowledge.project_connection_id == toolbox.KNOWLEDGE_CONNECTION
    project.toolboxes.update.assert_called_once_with(TOOLBOX, default_version="4")


def test_toolbox_defaults_to_shared_network_connection():
    import toolbox

    project = MagicMock()
    project.connections.get.return_value = SimpleNamespace(target="https://example.test/mcp")
    network, _ = toolbox.toolbox_tools(project)
    assert network.project_connection_id == "supportbuddy-network-mcp"


def test_toolbox_show_explains_mcp_routes_and_authentication(monkeypatch, capsys):
    import asyncio
    import toolbox

    route = {
        "server_label": "network",
        "server_url": "https://example.test/mcp",
        "project_connection_id": "supportbuddy-network-mcp-agent",
    }
    project = MagicMock()
    project.__enter__.return_value = project
    project.toolboxes.get.return_value.as_dict.return_value = {"default_version": "4"}
    project.toolboxes.get_version.return_value.as_dict.return_value = {"tools": [route]}
    project.connections.get.return_value.as_dict.return_value = {
        "credentials": {"type": "AgenticIdentityToken"}
    }
    monkeypatch.setattr(toolbox, "AIProjectClient", lambda **kwargs: project)

    class AsyncContext:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value or self

        async def __aexit__(self, *args):
            return None

    import agent_framework.foundry
    import azure.identity.aio

    monkeypatch.setattr(azure.identity.aio, "AzureCliCredential", lambda: AsyncContext(object()))
    monkeypatch.setattr(
        agent_framework.foundry,
        "FoundryToolbox",
        lambda credential, url: AsyncContext(SimpleNamespace(functions=[SimpleNamespace(name="network___identify_customer")])),
    )

    asyncio.run(toolbox.show("https://example.test/project", TOOLBOX, "https://example.test/toolboxes/mcp"))

    output = capsys.readouterr().out
    assert "Active version: 4" in output
    assert "Server endpoint: https://example.test/mcp" in output
    assert "Foundry connection: supportbuddy-network-mcp-agent" in output
    assert "hosted agent identity (AgenticIdentityToken)" in output
    assert "identify_customer  [network___identify_customer]" in output
    assert "does not prove a tool call succeeded" in output


def test_toolbox_show_reports_agent_identity_needs_hosted_context(monkeypatch, capsys):
    import asyncio
    import toolbox
    from agent_framework.exceptions import ToolExecutionException

    route = {
        "server_label": "network",
        "server_url": "https://example.test/mcp",
        "project_connection_id": "supportbuddy-network-mcp-agent",
    }
    project = MagicMock()
    project.__enter__.return_value = project
    project.toolboxes.get.return_value.as_dict.return_value = {"default_version": "6"}
    project.toolboxes.get_version.return_value.as_dict.return_value = {"tools": [route]}
    project.connections.get.return_value.as_dict.return_value = {
        "credentials": {"type": "AgenticIdentityToken"}
    }
    monkeypatch.setattr(toolbox, "AIProjectClient", lambda **kwargs: project)

    class AsyncContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return None

    class AgentContext:
        async def __aenter__(self):
            raise ToolExecutionException(
                "AgenticIdentityToken auth type requires AgentInstanceClientId and AgentBlueprintClientId"
            )

        async def __aexit__(self, *args):
            return None

    import agent_framework.foundry
    import azure.identity.aio

    monkeypatch.setattr(azure.identity.aio, "AzureCliCredential", AsyncContext)
    monkeypatch.setattr(agent_framework.foundry, "FoundryToolbox", lambda credential, url: AgentContext())

    asyncio.run(toolbox.show("https://example.test/project", TOOLBOX, "https://example.test/toolboxes/mcp"))

    output = capsys.readouterr().out
    assert "Active version: 6" in output
    assert "AgenticIdentityToken" in output
    assert "Tool listing requires the hosted-agent identity" in output
    assert "use a hosted conversation to verify these tools" in output


def test_toolbox_cli_forwards_selected_network_connection(monkeypatch):
    import toolbox

    project = MagicMock()
    project.__enter__.return_value = project
    publish = MagicMock()
    monkeypatch.setattr(sys, "argv", ["toolbox.py", "create", "--network-connection", "supportbuddy-network-mcp-agent"])
    monkeypatch.setattr(toolbox, "AIProjectClient", lambda **kwargs: project)
    monkeypatch.setattr(toolbox, "load_settings", lambda: {"AZURE_AI_PROJECT_ENDPOINT": "https://example.test"})
    monkeypatch.setattr(toolbox, "toolbox_settings", lambda: {"TOOLBOX_NAME": TOOLBOX, "NETWORK_TOOLS": "toolbox"})
    monkeypatch.setattr(toolbox, "create", publish)
    assert toolbox.main() == 0
    publish.assert_called_once_with(project, TOOLBOX, None, include_network=True,
                                    network_connection="supportbuddy-network-mcp-agent")


def test_hosted_agent_requirements_match_the_pinned_agent_framework():
    pyproject = (SRC / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (SRC / "requirements.txt").read_text(encoding="utf-8")
    for line in requirements.splitlines():
        if line.startswith("agent-framework"):
            assert f'"{line}"' in pyproject, line
