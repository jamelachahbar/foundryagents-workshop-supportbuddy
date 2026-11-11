"""The optional app-role check on the network MCP server (docs/optional-secure-mcp.md)."""

import base64
import json
import logging

import httpx
import pytest

from mcp_server import access
from mcp_server.server import create_app


def principal(*claims: tuple[str, str]) -> str:
    body = {"auth_typ": "aad", "claims": [{"typ": typ, "val": val} for typ, val in claims]}
    return base64.b64encode(json.dumps(body).encode()).decode()


async def ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def client(role: str = "Network.Read") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=access.RequireRole(ok_app, role))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(access.ROLE_SETTING, raising=False)
    assert access.protect(ok_app) is ok_app


def test_on_when_configured(monkeypatch):
    monkeypatch.setenv(access.ROLE_SETTING, "Network.Read")
    wrapped = access.protect(ok_app)
    assert isinstance(wrapped, access.RequireRole) and wrapped.role == "Network.Read"


@pytest.mark.anyio
async def test_server_app_enforces_configured_role(monkeypatch):
    monkeypatch.setenv(access.ROLE_SETTING, "Network.Read")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post("/mcp")
        no_role = await client.post("/mcp", headers={"X-MS-CLIENT-PRINCIPAL": principal(("roles", "Other.Role"))})
    assert response.status_code == 401
    assert no_role.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [{}, {"X-MS-CLIENT-PRINCIPAL": "not-base64!"},
                                     {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(b"{}").decode()}])
async def test_no_valid_principal_is_401(headers):
    async with client() as c:
        response = await c.post("/mcp", headers=headers)
    assert response.status_code == 401


@pytest.mark.anyio
async def test_missing_role_is_403(caplog):
    caplog.set_level(logging.INFO, logger="mcp_server.access")
    header = principal(("azp", "app-1"), ("oid", "obj-1"), ("roles", "Other.Role"))
    async with client() as c:
        response = await c.post("/mcp", headers={"X-MS-CLIENT-PRINCIPAL": header,
                                                 "Authorization": "Bearer secret-token-value"})
    assert response.status_code == 403
    assert "obj-1" in caplog.text and "secret-token-value" not in caplog.text
    assert response.headers["x-request-id"] in caplog.text
    assert "outcome=denied" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("claim", ["roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"])
async def test_role_passes(claim, caplog):
    caplog.set_level(logging.INFO, logger="mcp_server.access")
    header = principal(("appid", "app-2"), ("oid", "obj-2"), (claim, "Network.Read"))
    async with client() as c:
        response = await c.post("/mcp", headers={"X-MS-CLIENT-PRINCIPAL": header,
                                                 "Authorization": "Bearer secret-token-value"})
    assert response.status_code == 200 and response.text == "ok"
    assert "mcp_access" in caplog.text and "app=app-2 object=obj-2" in caplog.text
    assert "secret-token-value" not in caplog.text


@pytest.mark.anyio
async def test_allowed_tool_log_shares_request_id_with_caller(caplog):
    caplog.set_level(logging.INFO, logger="mcp_server.access")

    async def tool_app(scope, receive, send):
        result = access.execute_tool("identify_customer", lambda: {"found": True})
        assert result == {"found": True}
        await ok_app(scope, receive, send)

    header = principal(("azp", "agent-app"), ("oid", "agent-object"), ("roles", "Network.Read"))
    transport = httpx.ASGITransport(app=access.RequireRole(tool_app, "Network.Read"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.post("/mcp", headers={"X-MS-CLIENT-PRINCIPAL": header})

    request_id = response.headers["x-request-id"]
    assert response.status_code == 200
    assert f"mcp_access request_id={request_id} app=agent-app object=agent-object" in caplog.text
    assert f"mcp_tool request_id={request_id} app=agent-app object=agent-object" in caplog.text
    assert "tool=identify_customer outcome=success" in caplog.text


@pytest.fixture
def anyio_backend():
    return "asyncio"
