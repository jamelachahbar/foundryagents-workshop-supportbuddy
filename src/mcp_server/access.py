"""Optional caller check for the shared network MCP server (lab construct).

Off by default: the laptop copy (Stage 6) and the lab default on Azure stay open, as today.

When ``NETWORK_MCP_REQUIRED_ROLE`` is set (for example ``Network.Read``), every HTTP request must carry
a caller principal with that app role. Container Apps built-in authentication validates the Microsoft
Entra token (signature, issuer, audience) in front of the container and forwards the caller's claims
in the ``X-MS-CLIENT-PRINCIPAL`` header; it removes that header from incoming requests, so a client
cannot forge it. Set the role ONLY when Container Apps authentication is on for this app:
``instructor/secure_mcp.py enable --roles`` turns on both together, and ``disable`` removes both.

This is the server-side authorisation the MCP security guidance asks for: the platform proves who is
calling, the server decides what that caller may do. Each allowed or refused call is logged with the
caller's IDs, never with a token.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

logger = logging.getLogger("mcp_server.access")
PRINCIPAL_HEADER = b"x-ms-client-principal"
ROLE_SETTING = "NETWORK_MCP_REQUIRED_ROLE"


def caller(header: bytes | None) -> dict[str, Any] | None:
    """Decode the Container Apps principal header into {"roles": [...], "app": ..., "object": ...}."""
    if not header:
        return None
    try:
        principal = json.loads(base64.b64decode(header, validate=True))
        claims = principal["claims"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(claims, list):
        return None
    values: dict[str, list[str]] = {}
    for claim in claims:
        if isinstance(claim, dict) and isinstance(claim.get("typ"), str) and isinstance(claim.get("val"), str):
            values.setdefault(claim["typ"], []).append(claim["val"])
    roles = values.get("roles", []) + values.get("http://schemas.microsoft.com/ws/2008/06/identity/claims/role", [])
    first = lambda *names: next((values[n][0] for n in names if values.get(n)), None)
    return {
        "roles": roles,
        "app": first("azp", "appid"),
        "object": first("oid", "http://schemas.microsoft.com/identity/claims/objectidentifier"),
    }


class RequireRole:
    """ASGI middleware: refuse HTTP requests whose caller lacks the required app role."""

    def __init__(self, app: Any, role: str) -> None:
        self.app = app
        self.role = role

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = dict(scope.get("headers") or []).get(PRINCIPAL_HEADER)
        who = caller(header)
        if who is None:
            logger.warning("refused: no valid caller principal (is Container Apps authentication on?)")
            await _reply(send, 401, "Unauthorized: no authenticated caller.")
            return
        if self.role not in who["roles"]:
            logger.warning("refused: app=%s object=%s lacks role %s", who["app"], who["object"], self.role)
            await _reply(send, 403, f"Forbidden: the caller lacks the {self.role} role.")
            return
        logger.info("allowed: app=%s object=%s role=%s", who["app"], who["object"], self.role)
        await self.app(scope, receive, send)


async def _reply(send: Any, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def protect(app: Any) -> Any:
    """Wrap the MCP app when NETWORK_MCP_REQUIRED_ROLE is set; otherwise return it unchanged."""
    role = os.getenv(ROLE_SETTING, "").strip()
    return RequireRole(app, role) if role else app
