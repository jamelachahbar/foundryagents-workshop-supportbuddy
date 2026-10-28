"""Serve DevUI with the SupportBuddy name and icon instead of the default Agent Framework branding.

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
from agent_framework_devui import DevServer, serve
from starlette.responses import HTMLResponse
from starlette.routing import Route

from runtime_settings import app_url, devui_host

BRAND_NAME = "SupportBuddy"
ICON_FILE = Path(__file__).resolve().parents[1] / "docs" / "assets" / "icon.svg"

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
    if not ICON_FILE.is_file():
        serve(entities=[agent], port=port, host=host, auto_open=auto_open, auth_token=auth_token)
        return

    page = branded_index(ICON_FILE.read_text(encoding="utf-8"))
    server = DevServer(port=port, host=host, auth_token=auth_token)
    server.set_pending_entities([agent])
    app = server.get_app()
    # DevUI mounts its UI at "/" after its API routes; serve the branded start page ahead of it.
    app.router.routes.insert(0, Route("/", lambda _request: HTMLResponse(page), methods=["GET"]))

    if auto_open:
        threading.Timer(2.0, webbrowser.open, [app_url(port)]).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
