"""The optional app-role check on the network MCP server (docs/optional-secure-mcp.md)."""

import base64
import json
import logging

import httpx
import pytest

from mcp_server import access


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


@pytest.mark.anyio
@pytest.mark.parametrize("claim", ["roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"])
async def test_role_passes(claim, caplog):
    caplog.set_level(logging.INFO, logger="mcp_server.access")
    header = principal(("appid", "app-2"), ("oid", "obj-2"), (claim, "Network.Read"))
    async with client() as c:
        response = await c.post("/mcp", headers={"X-MS-CLIENT-PRINCIPAL": header,
                                                 "Authorization": "Bearer secret-token-value"})
    assert response.status_code == 200 and response.text == "ok"
    assert "allowed: app=app-2 object=obj-2" in caplog.text and "secret-token-value" not in caplog.text


@pytest.fixture
def anyio_backend():
    return "asyncio"
