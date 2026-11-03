"""The optional live lab must not depend on participant stage files."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent

import lab_agent
from contact_centre.evidence import EvidencePanel


@pytest.fixture
def lab_settings(monkeypatch):
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/lab")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "lab-model")
    monkeypatch.setenv("WORKSHOP_RESOURCE_PREFIX", "lab-test")
    monkeypatch.delenv("NETWORK_TOOLS", raising=False)


def test_optional_lab_loads_its_own_instructions_and_not_the_participant_module(monkeypatch, lab_settings):
    def participant_tools():
        raise AssertionError("The optional lab must not load participant tools.")

    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(tools=participant_tools, context_providers=Mock()))
    module = importlib.import_module("ag_ui_lab")
    build = Mock()
    monkeypatch.setattr(lab_agent, "build_agent", build)
    assert not lab_agent.stage_signs_in("ag_ui_lab")
    assert lab_agent.stage_agent("ag_ui_lab") is build.return_value
    script, tools = build.call_args.args
    assert Path(script).name == "ag_ui_lab.py"
    assert [tool.name for tool in tools] == [
        "lab-test-supportbuddy-tools", "escalate_to_human", "get_handover_status", "restart_modem",
    ]
    assert tools[0].tool_result_content == "both"
    instructions = lab_agent.load_instructions(module.__file__)
    assert "# Optional AG-UI lab baseline" in instructions
    assert instructions.startswith("# Synthetic scenario time")
    assert "one customer per conversation" in instructions
    assert "approval" in instructions
    assert "Never invent" in instructions
    assert "memory" not in instructions.lower()


def test_optional_lab_keeps_the_existing_local_network_fallback(monkeypatch, lab_settings):
    module = importlib.import_module("ag_ui_lab")
    monkeypatch.setenv("NETWORK_TOOLS", "local")
    monkeypatch.setenv("MOCK_MCP_URL", "http://127.0.0.1:8000/mcp")
    assert [tool.name for tool in module.tools()] == [
        "lab-test-supportbuddy-tools", "escalate_to_human", "get_handover_status", "restart_modem", "network",
    ]


def test_optional_lab_restart_still_requires_explicit_approval(lab_settings):
    module = importlib.import_module("ag_ui_lab")
    restart = next(tool for tool in module.tools() if tool.name == "restart_modem")
    assert restart.approval_mode == "always_require"


def test_contact_centre_launches_isolated_optional_profile(monkeypatch, lab_settings):
    from contact_centre import server

    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(context_providers=Mock(side_effect=AssertionError)))
    monkeypatch.setattr(sys, "argv", ["contact_centre", "ag_ui_lab", "--evidence-panel"])
    monkeypatch.setattr(server, "configure_local_tracing", lambda: None)
    serve = Mock()
    monkeypatch.setattr(server.uvicorn, "run", serve)
    server.main()
    with TestClient(serve.call_args.args[0]) as client:
        assert client.get("/api/config").json()["evidence_panel"] is True
        assert client.get("/chat").status_code == 200
        assert client.get("/desk").status_code == 200


@pytest.mark.parametrize("blocked", [False, True])
def test_optional_lab_projects_real_toolbox_parser_output(lab_settings, runbook_mcp_result, blocked):
    module = importlib.import_module("ag_ui_lab")
    toolbox = module.tools()[0]
    parsed = toolbox._parse_tool_result_from_mcp(CallToolResult(
        content=[TextContent(**item) for item in runbook_mcp_result],
        structuredContent={"raiPostToolAnnotations": {"blocked": blocked}},
    ))
    raw = [{"type": content.type, "text": content.text} for content in parsed]
    panel = EvidencePanel()
    panel.start("kb", "knowledge_base_retrieve", "run")
    panel.result("kb", None, raw)
    row = panel.snapshot()[0]
    assert row["status"] == ("failed" if blocked else "completed")
    if not blocked:
        assert row["finding"] == "Retrieval completed; 1 documents returned"
