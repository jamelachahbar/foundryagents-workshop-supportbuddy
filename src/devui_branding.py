"""Serve DevUI with SupportBuddy branding and Foundry remote-tool rendering.

DevUI has no official branding option. This replaces only its start page ("/"): same app, same
scripts, plus a new title, favicon and a small script that swaps the header logo and name.
If the page layout changes in a future DevUI release, the script simply finds nothing to swap.
"""

from __future__ import annotations

import base64
import html
import os
import threading
import webbrowser
from importlib.resources import files
from pathlib import Path
from typing import Any

import uvicorn
from agent_framework import Content
from agent_framework_devui import DevServer
from agent_framework_devui._executor import AgentFrameworkExecutor
from agent_framework_devui._mapper import MessageMapper
from starlette.responses import HTMLResponse
from starlette.routing import Route

from runtime_settings import app_url, devui_host

BRAND_NAME = "SupportBuddy"
ICON_FILE = Path(__file__).resolve().parents[1] / "docs" / "assets" / "icon.svg"


def register_foundry_renderers(mapper: MessageMapper) -> None:
    """Adapt remote MCP events for the pinned DevUI, without changing agent content or execution."""
    async def map_call(content: Content, context: dict[str, Any]) -> Any:
        if not content.call_id or not content.tool_name:
            raise ValueError("Foundry MCP tool call is missing its call ID or tool name.")
        display = Content.from_function_call(
            call_id=content.call_id,
            name=content.tool_name,
            arguments=content.arguments,
            informational_only=True,
            hosted=True,
            server_name=content.server_name,
        )
        return await mapper._map_function_call_content(display, context)

    async def map_result(content: Content, context: dict[str, Any]) -> Any:
        if not content.call_id:
            raise ValueError("Foundry MCP tool result is missing its call ID.")
        display = Content.from_function_result(
            call_id=content.call_id,
            result=content.output,
            exception=content.exception,
        )
        return await mapper._map_function_result_content(display, context)

    # DevUI has no public mapper-extension API; keep this compatibility shim at its display boundary.
    mapper.content_mappers.setdefault("mcp_server_tool_call", map_call)
    mapper.content_mappers.setdefault("mcp_server_tool_result", map_result)


class SupportBuddyDevServer(DevServer):
    async def _ensure_executor(self) -> AgentFrameworkExecutor:
        executor = await super()._ensure_executor()
        register_foundry_renderers(executor.message_mapper)
        return executor


_SWAP_HEADER = """
<script>
(() => {
  const ICON = "%(icon)s", NAME = "%(name)s";
  const swap = () => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.nodeValue !== "Dev UI") continue;
      node.nodeValue = NAME;
      const logo = node.parentElement && node.parentElement.querySelector(":scope > svg");
      if (logo && !logo.dataset.branded) {
        logo.dataset.branded = "1";
        const box = logo.getBoundingClientRect();
        const img = document.createElement("img");
        img.src = ICON;
        img.alt = NAME;
        img.width = Math.round(box.width) || 20;
        img.height = Math.round(box.height) || 20;
        img.style.borderRadius = "22%%";
        logo.style.display = "none";
        logo.before(img);
      }
    }
  };
  new MutationObserver(swap).observe(document.body, { childList: true, subtree: true });
  swap();
})();
</script>
"""


def branded_index(icon_svg: str) -> str:
    """DevUI's own index.html with the SupportBuddy title, favicon and header swap."""
    icon = "data:image/svg+xml;base64," + base64.b64encode(icon_svg.encode("utf-8")).decode("ascii")
    page = (files("agent_framework_devui") / "ui" / "index.html").read_text(encoding="utf-8")
    page = page.replace("<title>Agent Framework Dev UI</title>", f"<title>{html.escape(BRAND_NAME)} · DevUI</title>")
    page = page.replace('href="agentframework.svg"', f'href="{icon}"')
    return page.replace("</body>", _SWAP_HEADER % {"icon": icon, "name": BRAND_NAME} + "</body>")


def serve_branded(agent: Any, port: int, auth_token: str) -> None:
    """Run authenticated DevUI with SupportBuddy branding (plain DevUI if the icon is missing)."""
    host = devui_host()
    auto_open = os.getenv("CODESPACES") != "true"
    server = SupportBuddyDevServer(port=port, host=host, auth_token=auth_token)
    server.set_pending_entities([agent])
    app = server.get_app()
    if ICON_FILE.is_file():
        page = branded_index(ICON_FILE.read_text(encoding="utf-8"))
        # DevUI mounts its UI at "/" after its API routes; serve the branded start page ahead of it.
        app.router.routes.insert(0, Route("/", lambda _request: HTMLResponse(page), methods=["GET"]))

    if auto_open:
        threading.Timer(2.0, webbrowser.open, [app_url(port)]).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
