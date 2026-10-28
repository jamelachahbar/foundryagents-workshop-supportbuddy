"""SupportBuddy contact centre: customer chat, operator desk and the agents behind them (Stage 7).

LAB CONSTRUCT. It stands in for an engagement hub such as Dynamics 365 Contact Center:
  - /chat  the customer's chat window;
  - /desk  the human advisor's workspace: live queue, live chat with the customer, advisor assist;
  - /ag-ui/...  SupportBuddy and the advisor assist agent, served over the AG-UI protocol.

The contact centre owns every conversation and decides who answers: SupportBuddy, or a human advisor
after a handover. It binds to 127.0.0.1 and has no sign-in: in production the advisor signs in with
Microsoft Entra ID and the customer through the channel.

Run it from the src folder, with the agent of your current stage:
    uv run python -m contact_centre                       # your main.py
    uv run python -m contact_centre solutions.stage_9_toolbox
    uv run python -m contact_centre --prompt-agent        # your Foundry prompt agent (Stage 10)
Then open http://127.0.0.1:8070
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import uvicorn
from agent_framework import SupportsAgentRun
from agent_framework_ag_ui import AgentFrameworkAgent, add_agent_framework_fastapi_endpoint
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from contact_centre.centre import ASSIST_PATH, SUPPORTBUDDY_PATH, UNIDENTIFIED, ContactCentre, http_runner
from lab_backend import backend
from runtime_settings import app_url

logger = logging.getLogger("contact_centre")
STATIC = Path(__file__).parent / "static"
ICON = Path(__file__).resolve().parents[2] / "docs" / "assets" / "icon.svg"
CONVERSATION_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_TEXT = 2000
MAX_ADVISOR = 60
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
PAGES = {
    "/": ("index.html", "text/html"),
    "/chat": ("chat.html", "text/html"),
    "/desk": ("desk.html", "text/html"),
    "/chat.js": ("chat.js", "text/javascript"),
    "/desk.js": ("desk.js", "text/javascript"),
    "/common.js": ("common.js", "text/javascript"),
    "/centre.css": ("centre.css", "text/css"),
}


class BadRequest(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def create_app(
    centre: ContactCentre,
    agents: dict[str, SupportsAgentRun],
    *,
    customers: list[dict[str, str]] | None = None,
) -> FastAPI:
    exit_stack = contextlib.AsyncExitStack()
    opened: dict[str, asyncio.Lock] = {}
    is_open: set[str] = set()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with exit_stack:
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    tasks: set[asyncio.Task[None]] = set()
    known_customers = {c["customer_id"] for c in customers or []}

    for path, agent in agents.items():
        # Approvals travel as AG-UI interrupts; the synthetic confirm_changes tool call is not needed.
        add_agent_framework_fastapi_endpoint(app, AgentFrameworkAgent(agent, require_confirmation=False), path)

    async def open_agent(path: str) -> None:
        """Open an agent's connections (MCP, toolbox, memory) the first time a conversation needs it."""
        if path in is_open or path not in agents:
            return
        async with opened.setdefault(path, asyncio.Lock()):
            if path not in is_open:
                from lab_agent import open_agent as open_lab_agent

                await exit_stack.enter_async_context(open_lab_agent(agents[path]))
                is_open.add(path)

    centre.opener = open_agent

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith("/ag-ui/"):
            response.headers.update(SECURITY_HEADERS)
        return response

    @app.exception_handler(BadRequest)
    async def bad_request(_: Request, error: BadRequest) -> JSONResponse:
        return JSONResponse({"error": str(error)}, status_code=error.status)

    for route, (name, media_type) in PAGES.items():
        app.add_api_route(route, _page(name, media_type), methods=["GET"], include_in_schema=False)

    @app.get("/icon.svg", include_in_schema=False)
    async def icon() -> Response:
        return FileResponse(ICON, media_type="image/svg+xml")

    # ----- helpers ---------------------------------------------------------------------------

    async def body(request: Request) -> dict[str, Any]:
        # JSON only: a browser cannot send it cross-site without a CORS preflight, which this app never allows.
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise BadRequest("Expected application/json.", 415)
        data = await request.json()
        if not isinstance(data, dict):
            raise BadRequest("Expected a JSON object.")
        return data

    def text_field(data: dict[str, Any], name: str, limit: int) -> str:
        value = str(data.get(name, "")).strip()
        if not value or len(value) > limit:
            raise BadRequest(f"'{name}' is required (at most {limit} characters).")
        return value

    def conversation(conversation_id: str):  # noqa: ANN202
        found = centre.store.get(conversation_id) if CONVERSATION_ID.match(conversation_id) else None
        if not found:
            raise BadRequest("Conversation not found.", 404)
        return found

    def in_background(work: Awaitable[None]) -> None:
        async def run() -> None:
            try:
                await work
            except Exception:
                logger.exception("Background work failed")
            finally:
                centre.store.bus.publish({"type": "actions", "actions": backend.list_actions()})

        task = asyncio.create_task(run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def check(action: Callable[[], Any]) -> Any:
        try:
            return action()
        except (ValueError, LookupError) as error:
            raise BadRequest(str(error), 409) from error

    def approval_fields(data: dict[str, Any]) -> tuple[str, bool]:
        interrupt_id = text_field(data, "interrupt_id", 200)
        if not isinstance(data.get("approved"), bool):
            raise BadRequest("'approved' must be true or false.")
        return interrupt_id, data["approved"]

    # ----- customer chat ---------------------------------------------------------------------

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        return {"agent": centre.agent_label, "signs_in": centre.signs_in, "customers": customers or []}

    @app.post("/api/conversations")
    async def start(request: Request) -> dict[str, Any]:
        data = await body(request)
        customer_id = str(data.get("customer_id") or "").strip().upper() or None
        if customer_id and customer_id not in known_customers:
            raise BadRequest("Unknown customer.")
        found = check(lambda: centre.start(customer_id))
        return found.customer_view()

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202)
    async def customer_message(conversation_id: str, request: Request) -> dict[str, str]:
        found = conversation(conversation_id)
        text = text_field(await body(request), "text", MAX_TEXT)
        if found.mode == "closed" or found.busy:
            raise BadRequest("Please wait: the conversation cannot take a message right now.", 409)
        in_background(centre.customer_message(found, text))
        return {"status": "accepted"}

    @app.post("/api/conversations/{conversation_id}/approvals", status_code=202)
    async def customer_approval(conversation_id: str, request: Request) -> dict[str, str]:
        found = conversation(conversation_id)
        interrupt_id, approved = approval_fields(await body(request))
        check(lambda: centre._pending_approval(found.transcript, interrupt_id))
        in_background(centre.customer_approval(found, interrupt_id, approved))
        return {"status": "accepted"}

    @app.get("/api/conversations/{conversation_id}/events")
    async def customer_events(conversation_id: str) -> StreamingResponse:
        found = conversation(conversation_id)

        def visible(event: dict[str, Any]) -> bool:
            # The customer only sees their own conversation, never the advisor's assist thread.
            return event.get("conversation") == found.id and event["type"] in ("message", "conversation")

        return _sse(centre, {"type": "snapshot", "conversation": found.customer_view()}, visible)

    # ----- operator desk ---------------------------------------------------------------------

    @app.get("/api/desk/events")
    async def desk_events() -> StreamingResponse:
        snapshot = {
            "type": "snapshot",
            "conversations": [c.desk_view() for c in centre.store.conversations.values()],
            "actions": backend.list_actions(),
        }
        return _sse(centre, snapshot, lambda _: True)

    @app.post("/api/desk/conversations/{conversation_id}/{action}")
    async def desk_action(conversation_id: str, action: str, request: Request) -> dict[str, str]:
        found = conversation(conversation_id)
        data = await body(request)
        advisor = text_field(data, "advisor", MAX_ADVISOR)
        if action == "take":
            check(lambda: centre.take(found, advisor))
        elif action == "messages":
            text = text_field(data, "text", MAX_TEXT)
            check(lambda: centre.advisor_message(found, advisor, text))
        elif action == "handback":
            check(lambda: centre.hand_back(found, advisor))
        elif action == "close":
            check(lambda: centre.close(found, advisor))
        elif action == "assist":
            text = text_field(data, "text", MAX_TEXT)
            if found.assist_busy:
                raise BadRequest("The assist agent is still working.", 409)
            in_background(centre.assist(found, advisor, text))
        elif action == "assist-approvals":
            interrupt_id, approved = approval_fields(data)
            check(lambda: centre._pending_approval(found.assist_transcript, interrupt_id))
            in_background(centre.assist_approval(found, advisor, interrupt_id, approved))
        else:
            raise BadRequest("Unknown action.", 404)
        return {"status": "ok"}

    return app


def _page(name: str, media_type: str) -> Callable[[], Awaitable[Response]]:
    async def page() -> Response:
        return FileResponse(STATIC / name, media_type=media_type)

    return page


def _sse(centre: ContactCentre, first: dict[str, Any], visible: Callable[[dict[str, Any]], bool]) -> StreamingResponse:
    queue = centre.store.bus.subscribe()

    async def stream() -> AsyncIterator[str]:
        try:
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if visible(event):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            centre.store.bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


def main() -> None:
    parser = argparse.ArgumentParser(description="SupportBuddy contact centre (Stage 7 and later).")
    parser.add_argument("module", nargs="?", default="main", help="Stage file to run, e.g. main or solutions.stage_9_toolbox.")
    parser.add_argument("--prompt-agent", action="store_true", help="Use your Foundry prompt agent (Stage 10) instead.")
    args = parser.parse_args()

    from contact_centre.assist import build_assist_agent
    from lab_agent import stage_agent, stage_signs_in

    port = int(os.getenv("CONTACT_CENTRE_PORT", "8070"))
    customers = [{"customer_id": c["customer_id"], "name": c["name"]} for c in backend.list_customers()]
    agents: dict[str, SupportsAgentRun] = {ASSIST_PATH.format(who=UNIDENTIFIED): build_assist_agent(None)}
    for customer in customers:
        agents[ASSIST_PATH.format(who=customer["customer_id"])] = build_assist_agent(customer["customer_id"])
    if args.prompt_agent:
        from prompt_agent import connect_prompt_agent, prompt_agent_name

        signs_in, label = False, f"prompt agent {prompt_agent_name()}"
        agents[SUPPORTBUDDY_PATH.format(who="guest")] = connect_prompt_agent()
    else:
        signs_in, label = stage_signs_in(args.module), args.module
        if signs_in:
            for customer in customers:
                agents[SUPPORTBUDDY_PATH.format(who=customer["customer_id"])] = stage_agent(args.module, customer["customer_id"])
        else:
            agents[SUPPORTBUDDY_PATH.format(who="guest")] = stage_agent(args.module)

    centre = ContactCentre(http_runner(f"http://127.0.0.1:{port}"), signs_in=signs_in, agent_label=label)
    app = create_app(centre, agents, customers=customers)
    print(
        f"\nContact centre for {label}"
        f"\n  Customer chat:  {app_url(port)}/chat"
        f"\n  Operator desk:  {app_url(port)}/desk\n",
        flush=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
