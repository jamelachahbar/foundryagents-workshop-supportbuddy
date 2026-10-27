"""The MCP server must expose the same capability contract the agent used as local functions."""

import json
import socket
import threading
import time

import pytest
import uvicorn
from agent_framework import MCPStreamableHTTPTool
from mcp.shared.memory import create_connected_server_and_client_session as connect

from lab_backend import backend
from mcp_server.server import mcp
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


@pytest.fixture
def running_server():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")
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


@pytest.mark.anyio
async def test_agent_framework_reaches_the_server_over_streamable_http(running_server):
    """Same client the Stage 6 agent uses, over real HTTP, without a model in the loop."""
    async with MCPStreamableHTTPTool(name="network", url=running_server, load_prompts=False) as tool:
        assert sorted(f.name for f in tool.functions) == sorted(DIAGNOSTIC_TOOLS)
        result = await tool.call_tool("identify_customer", email="anna.weber@example.com")

    text = result if isinstance(result, str) else "".join(c.text or "" for c in result)
    payload = json.loads(text)
    assert payload["found"] is True
    assert payload["customer"]["customer_id"] == "CUST-1001"
    assert payload["source"] == "mock"
