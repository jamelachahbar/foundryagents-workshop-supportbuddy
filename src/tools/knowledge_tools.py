"""The Foundry IQ knowledge base as an MCP tool (Stage 8).

A Foundry IQ knowledge base is an MCP server: it exposes one tool, ``knowledge_base_retrieve``,
at ``<search endpoint>/knowledgebases/<name>/mcp``. The agent connects to it exactly like it
connected to the network MCP server in Stage 6. The only new thing is authentication: Azure AI
Search wants a Microsoft Entra token, so every request carries your identity's bearer token.

The knowledge base holds synthetic troubleshooting runbooks (LAB CONSTRUCT, not real operator procedures).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx
from agent_framework import MCPStreamableHTTPTool
from azure.identity.aio import AzureCliCredential, get_bearer_token_provider

MCP_API_VERSION = "2026-08-01-preview"  # the knowledge base MCP endpoint is in preview
SEARCH_SCOPE = "https://search.azure.com/.default"


def knowledge_base_mcp_url(search_endpoint: str, knowledge_base: str) -> str:
    return f"{search_endpoint.rstrip('/')}/knowledgebases/{knowledge_base}/mcp?api-version={MCP_API_VERSION}"


class EntraBearerAuth(httpx.Auth):
    """Adds ``Authorization: Bearer <token>`` to every request. Tokens are cached and refreshed."""

    def __init__(self, token_provider: Callable[[], Awaitable[str]]) -> None:
        self._token_provider = token_provider

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {await self._token_provider()}"
        yield request


def runbook_knowledge_base(search_endpoint: str, knowledge_base: str) -> MCPStreamableHTTPTool:
    """MCP connection to the runbook knowledge base, authenticated as the signed-in Azure CLI user."""
    token_provider = get_bearer_token_provider(AzureCliCredential(), SEARCH_SCOPE)
    return MCPStreamableHTTPTool(
        name="runbooks",
        url=knowledge_base_mcp_url(search_endpoint, knowledge_base),
        http_client=httpx.AsyncClient(auth=EntraBearerAuth(token_provider), timeout=60),
        allowed_tools=["knowledge_base_retrieve"],
        approval_mode="never_require",  # read-only retrieval
        load_prompts=False,
    )

