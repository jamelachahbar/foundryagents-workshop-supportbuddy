"""SupportBuddy operator desk: the human side of the handover (Stage 7).

LAB CONSTRUCT. A small web app where a human advisor sees the cases the agent handed over,
with the evidence the agent collected, takes a case, and replies to the customer. The agent
reads that reply with get_handover_status. It also lists the actions agents ran after approval.

It reads and writes the same synthetic queue as the agent's tools (src/.lab_state/handovers.json),
so it only sees agents running on this machine. It binds to 127.0.0.1 and has no login: it is a
training tool, not a contact-centre product.

Run it from the src folder, in its own terminal:
    uv run python -m operator_desk
Then open http://127.0.0.1:8070
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from lab_backend import backend

STATIC = Path(__file__).parent / "static"
ICON = Path(__file__).resolve().parents[2] / "docs" / "assets" / "icon.svg"
HANDOVER_ID = re.compile(r"^HO-\d{4,6}$")
MAX_ADVISOR = 60
MAX_MESSAGE = 1000
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _static(name: str, media_type: str) -> FileResponse:
    return FileResponse(STATIC / name, media_type=media_type, headers=SECURITY_HEADERS)


async def index(_: Request) -> Response:
    return _static("index.html", "text/html")


async def script(_: Request) -> Response:
    return _static("desk.js", "text/javascript")


async def stylesheet(_: Request) -> Response:
    return _static("desk.css", "text/css")


async def icon(_: Request) -> Response:
    return FileResponse(ICON, media_type="image/svg+xml", headers=SECURITY_HEADERS)


async def handovers(_: Request) -> Response:
    return JSONResponse({"handovers": backend.list_handovers(), "actions": backend.list_actions()})


async def _form(request: Request) -> dict[str, str] | Response:
    # JSON only: browsers cannot send it cross-site without a CORS preflight, which this app never allows.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return JSONResponse({"error": "Expected application/json."}, status_code=415)
    body = await request.json()
    advisor = str(body.get("advisor", "")).strip()
    message = str(body.get("message", "")).strip()
    if not advisor or len(advisor) > MAX_ADVISOR or len(message) > MAX_MESSAGE:
        return JSONResponse({"error": "Advisor name is required; keep the message under 1000 characters."}, 400)
    return {"advisor": advisor, "message": message}


async def update(request: Request) -> Response:
    handover_id = request.path_params["handover_id"]
    action = request.path_params["action"]
    if not HANDOVER_ID.match(handover_id) or action not in ("take", "resolve"):
        return JSONResponse({"error": "Not found."}, status_code=404)
    form = await _form(request)
    if isinstance(form, Response):
        return form
    if action == "resolve" and not form["message"]:
        return JSONResponse({"error": "Write a message to the customer before resolving."}, status_code=400)
    updated = backend.update_handover(
        handover_id,
        status="in_progress" if action == "take" else "resolved",
        advisor=form["advisor"],
        advisor_message=form["message"] or None,
    )
    if updated is None:
        return JSONResponse({"error": "Not found."}, status_code=404)
    return JSONResponse(updated)


app = Starlette(
    routes=[
        Route("/", index),
        Route("/desk.js", script),
        Route("/desk.css", stylesheet),
        Route("/icon.svg", icon),
        Route("/api/handovers", handovers),
        Route("/api/handovers/{handover_id}/{action}", update, methods=["POST"]),
    ]
)


def main() -> None:
    port = int(os.getenv("OPERATOR_DESK_PORT", "8070"))
    print(f"\nOperator desk: http://127.0.0.1:{port}\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
