"""The MCP server must expose the same capability contract the agent used as local functions."""

import base64
import json
import logging
import socket
import threading
import time

import pytest
import uvicorn
from agent_framework import MCPStreamableHTTPTool
from mcp.shared.memory import create_connected_server_and_client_session as connect

from lab_backend import backend
from mcp_server import access
from mcp_server.server import create_app, mcp
from tools import support_tools

DIAGNOSTIC_TOOLS = ("identify_customer", "get_line_status", "check_equipment", "get_known_incidents")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_server_exposes_exactly_the_four_diagnostic_capabilities():
    async with connect(mcp._mcp_server) as client:
        listed = (await client.list_tools()).tools
    assert sorted(t.name for t in listed) == sorted(DIAGNOSTIC_TOOLS)


@pytest.mark.anyio
async def test_mcp_and_local_tools_share_the_same_contract():
    async with connect(mcp._mcp_server) as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}

    for name in DIAGNOSTIC_TOOLS:
        local = getattr(support_tools, name)
        local_schema = local.parameters()
        remote_schema = listed[name].inputSchema
        assert set(remote_schema["properties"]) == set(local_schema["properties"]), name
        assert set(remote_schema.get("required", [])) == set(local_schema.get("required", [])), name
        assert listed[name].description == local.description, name


@pytest.mark.anyio
async def test_mcp_tool_returns_the_backend_result():
    async with connect(mcp._mcp_server) as client:
        result = await client.call_tool("check_equipment", {"customer_id": "CUST-1002"})
    assert result.isError is False
    assert result.structuredContent == backend.check_equipment("CUST-1002")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("MCP server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def running_server(monkeypatch):
    monkeypatch.setenv(access.ROLE_SETTING, "Network.Read")
    yield from _serve(create_app())


@pytest.mark.anyio
async def test_agent_framework_reaches_the_server_and_correlates_tool_logs(running_server, caplog):
    """Same client the Stage 6 agent uses, over real HTTP, without a model in the loop."""
    caplog.set_level(logging.INFO, logger="mcp_server.access")
    identity = {"auth_typ": "aad", "claims": [
        {"typ": "azp", "val": "hosted-agent-app"},
        {"typ": "oid", "val": "hosted-agent-object"},
        {"typ": "roles", "val": "Network.Read"},
    ]}
    principal_header = base64.b64encode(json.dumps(identity).encode()).decode()
    async with MCPStreamableHTTPTool(
        name="network",
        url=running_server,
        load_prompts=False,
        static_headers={"X-MS-CLIENT-PRINCIPAL": principal_header},
    ) as tool:
        assert sorted(f.name for f in tool.functions) == sorted(DIAGNOSTIC_TOOLS)
        result = await tool.call_tool("identify_customer", email="anna.weber@example.com")

    text = result if isinstance(result, str) else "".join(c.text or "" for c in result)
    payload = json.loads(text)
    assert payload["found"] is True
    assert payload["customer"]["customer_id"] == "CUST-1001"
    assert payload["source"] == "mock"

    access_logs = [
        record.getMessage() for record in caplog.records
        if "mcp_access" in record.getMessage() and "app=hosted-agent-app" in record.getMessage()
    ]
    tool_log = next(
        record.getMessage() for record in caplog.records
        if "mcp_tool" in record.getMessage() and "tool=identify_customer" in record.getMessage()
    )
    tool_request_id = tool_log.split("request_id=", 1)[1].split(" ", 1)[0]

    assert any(f"request_id={tool_request_id} " in message for message in access_logs)
    assert "object=hosted-agent-object" in tool_log
    assert "tool=identify_customer outcome=success" in tool_log
