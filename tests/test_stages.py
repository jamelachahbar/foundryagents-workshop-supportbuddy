"""Each stage adds capabilities in the intended order, without needing Azure."""

import importlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
LOCAL_DIAGNOSTICS = ["identify_customer", "get_line_status", "get_known_incidents", "check_equipment"]

TOOLBOX = "post-test-supportbuddy-tools"

HUMAN_IN_THE_LOOP = ["escalate_to_human", "get_handover_status", "restart_modem"]

EXPECTED_TOOLS = {
    "stage_1_agent_only": [],
    "stage_2_identify_customer": ["identify_customer"],
    "stage_3_back_office": ["identify_customer", "get_line_status"],
    "stage_4_diagnostic": ["identify_customer", "get_line_status", "check_equipment"],
    "stage_5_correlation": LOCAL_DIAGNOSTICS,
    "stage_6_mcp": ["post_network"],
    "stage_7_answer_or_handoff": ["post_network", *HUMAN_IN_THE_LOOP],
    "stage_8_knowledge": ["post_network", "runbooks", *HUMAN_IN_THE_LOOP],
    "stage_9_toolbox": [TOOLBOX, *HUMAN_IN_THE_LOOP],
    "stage_11_memory": [TOOLBOX, *HUMAN_IN_THE_LOOP],
    "stage_13_guardrails": [TOOLBOX, *HUMAN_IN_THE_LOOP],
}


@pytest.fixture(autouse=True)
def _lab_settings(monkeypatch):
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/lab")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "lab-model")
    monkeypatch.setenv("WORKSHOP_RESOURCE_PREFIX", "post-test")
    monkeypatch.setenv("POST_MOCK_MCP_URL", "http://127.0.0.1:8000/mcp")
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
    assert memory.memory_store_name == "post-lab-memory"
    assert memory.scope == "post-test_CUST-1002"


@pytest.mark.parametrize("customer_id", ["", "CUST-1002 OR 1=1", "post-other_CUST-1002", "cust-12"])
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
    assert [t.name for t in module.tools()] == [TOOLBOX, *HUMAN_IN_THE_LOOP, "post_network"]


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
    assert agent.name == "post-test-supportbuddy"


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
    assert name == "post-test-supportbuddy-prompt"
    assert default.model == "lab-model"
    (toolbox,) = default.tools
    assert toolbox.server_url == "https://toolbox.example/mcp"
    assert "You have no handover tool" in default.instructions
    # The experiment declares the Python functions (name + JSON schema); the application runs them.
    assert [t.name for t in experiment.tools[1:]] == HUMAN_IN_THE_LOOP
    assert all(t.parameters["type"] == "object" for t in experiment.tools[1:])


def test_hosted_agent_uses_the_toolbox_and_the_local_handover_function(monkeypatch):
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/lab")
    import hosted_agent

    assert [t.name for t in hosted_agent.tools()] == [TOOLBOX, *HUMAN_IN_THE_LOOP]
    assert hosted_agent.create_agent().name == "post-test-supportbuddy"


def test_hosted_agent_requirements_match_the_pinned_agent_framework():
    pyproject = (SRC / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (SRC / "requirements.txt").read_text(encoding="utf-8")
    for line in requirements.splitlines():
        if line.startswith("agent-framework"):
            assert f'"{line}"' in pyproject, line
