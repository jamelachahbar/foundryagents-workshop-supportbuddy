"""Synthetic "network operations" MCP server for Stage 6.

LAB CONSTRUCT: exposes the same four diagnostic capabilities the agent used as
local Python functions in Stages 2-5, now behind the Model Context Protocol.
The tool contracts (names, parameters, return shapes) are unchanged, so the
agent's instructions do not change either - only where the capability lives.

This is not a production system. It reads the same synthetic data as
``lab_backend`` and has no authentication. On a laptop it binds to 127.0.0.1.
From Stage 9 the instructor also runs one shared copy on Azure Container Apps
so Foundry (Toolbox, prompt agents, hosted agents) can reach it; that copy only
serves the same fictional, read-only data. Securing and governing real MCP
endpoints (Entra ID, API Management / MCP gateway, rate limits) is covered in
the workshop discussion.

Run it from the ``src`` folder:

    uv run python -m mcp_server.server

The endpoint is then http://127.0.0.1:8000/mcp (streamable HTTP).
"""

from __future__ import annotations

import os
from typing import Annotated, Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from lab_backend import backend
from mcp_server.access import execute_tool, protect

mcp = FastMCP(
    "network-mock",
    instructions=(
        "Synthetic network-operations capabilities for a training lab. "
        "All data is fictional; this is not a production system."
    ),
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
    # No per-client session state, so any replica can answer any request.
    stateless_http=True,
)


@mcp.tool()
def identify_customer(
    customer_id: Annotated[str | None, Field(description="Customer ID, for example CUST-1001.")] = None,
    email: Annotated[str | None, Field(description="Customer e-mail address.")] = None,
    phone: Annotated[str | None, Field(description="Customer phone number, any format.")] = None,
) -> dict[str, Any]:
    """Identify the customer in the synthetic customer system (lab capability).

    Provide at least one identifier given by the customer. Returns the customer's
    profile, including the IDs of their internet lines (services).
    """
    return execute_tool(
        "identify_customer",
        lambda: backend.identify_customer(customer_id=customer_id, email=email, phone=phone),
    )


@mcp.tool()
def get_line_status(
    line_id: Annotated[str, Field(description="Line (service) ID, for example LINE-2001.")],
) -> dict[str, Any]:
    """Get the status of a customer's internet line from the synthetic service inventory (lab capability)."""
    return execute_tool("get_line_status", lambda: backend.get_line_status(line_id))


@mcp.tool()
def check_equipment(
    customer_id: Annotated[str, Field(description="Customer ID returned by identify_customer.")],
) -> dict[str, Any]:
    """Ping the customer's equipment (simulated) and return reachability and Wi-Fi diagnostics (lab capability)."""
    return execute_tool("check_equipment", lambda: backend.check_equipment(customer_id))


@mcp.tool()
def get_known_incidents(
    postcode: Annotated[str | None, Field(description="Customer postcode, for example MV-0001.")] = None,
    area: Annotated[str | None, Field(description="Customer area name.")] = None,
) -> dict[str, Any]:
    """List known network incidents for a postcode or area from the synthetic operations system (lab capability)."""
    return execute_tool(
        "get_known_incidents",
        lambda: backend.get_known_incidents(postcode=postcode, area=area),
    )


def create_app():
    return protect(mcp.streamable_http_app())


if __name__ == "__main__":
    uvicorn.run(create_app(), host=mcp.settings.host, port=mcp.settings.port)
