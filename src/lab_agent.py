"""Shared plumbing for every stage of the lab.

Keeps configuration, the Foundry connection and the "how do I run it" code in
one place so each stage file only shows what changes: the agent's
instructions and its tools.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import functools
import importlib
import json
import logging
import os
import re
import secrets
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from agent_framework import (
    Agent,
    AgentContext,
    AgentMiddleware,
    AgentResponseUpdate,
    AgentSession,
    ContextProvider,
    Message,
    SessionContext,
    SupportsAgentRun,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework_devui import register_cleanup
from azure.identity import AzureCliCredential
from dotenv import load_dotenv

from devui_branding import serve_branded

AGENT_BASE_NAME = "supportbuddy"
PREFIX_PATTERN = re.compile(r"^post-[a-z0-9]{2,8}$")
DEVUI_TOKEN_FILE = Path(__file__).parent / ".devui_token"
CUSTOMER_ID_PATTERN = re.compile(r"^CUST-\d{4}$")
DEFAULT_MEMORY_STORE_NAME = "post-lab-memory"
DEFAULT_KNOWLEDGE_BASE_NAME = "supportbuddy-runbooks"
REQUIRED_SETTINGS = (
    "AZURE_AI_PROJECT_ENDPOINT",
    "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    "WORKSHOP_RESOURCE_PREFIX",
)


def load_settings() -> dict[str, str]:
    """Read the lab settings from ``src/.env`` (endpoints and names only, never secrets)."""
    load_dotenv(Path(__file__).parent / ".env")
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
    if missing:
        sys.exit(f"Missing settings in src/.env: {', '.join(missing)}. Copy .env.template to .env first.")
    prefix = os.environ["WORKSHOP_RESOURCE_PREFIX"]
    if not PREFIX_PATTERN.match(prefix):
        sys.exit(f"WORKSHOP_RESOURCE_PREFIX={prefix!r} must look like post-<alias> (2-8 lowercase letters/digits).")
    return {name: os.environ[name] for name in REQUIRED_SETTINGS}


def knowledge_settings() -> dict[str, str]:
    """Read the Foundry IQ knowledge base settings used from Stage 8."""
    load_settings()
    endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
    if not endpoint:
        sys.exit("Missing AZURE_AI_SEARCH_ENDPOINT in src/.env (the Azure AI Search service behind Foundry IQ).")
    return {
        "AZURE_AI_SEARCH_ENDPOINT": endpoint.rstrip("/"),
        "FOUNDRY_KNOWLEDGE_BASE_NAME": os.getenv("FOUNDRY_KNOWLEDGE_BASE_NAME", DEFAULT_KNOWLEDGE_BASE_NAME),
    }


def memory_settings() -> dict[str, str]:
    """Read the Foundry Memory settings used from Stage 11."""
    load_settings()
    embedding = os.getenv("AZURE_AI_EMBEDDING_DEPLOYMENT_NAME")
    if not embedding:
        sys.exit("Missing AZURE_AI_EMBEDDING_DEPLOYMENT_NAME in src/.env (needed for Foundry Memory).")
    return {
        "FOUNDRY_MEMORY_STORE_NAME": os.getenv("FOUNDRY_MEMORY_STORE_NAME", DEFAULT_MEMORY_STORE_NAME),
        "AZURE_AI_EMBEDDING_DEPLOYMENT_NAME": embedding,
    }


def toolbox_settings() -> dict[str, str]:
    """Your Foundry Toolbox (Stage 9): one per participant, named after your prefix."""
    settings = load_settings()
    name = f"{settings['WORKSHOP_RESOURCE_PREFIX']}-{AGENT_BASE_NAME}-tools"
    # The consumer endpoint always serves the toolbox's default version.
    endpoint = f"{settings['AZURE_AI_PROJECT_ENDPOINT'].rstrip('/')}/toolboxes/{name}/mcp?api-version=v1"
    # "local" when the lab environment has no network MCP server on Azure: those tools then stay on your laptop.
    network_tools = os.getenv("NETWORK_TOOLS", "toolbox").strip().lower()
    if network_tools not in ("toolbox", "local"):
        sys.exit(f"NETWORK_TOOLS={network_tools!r} must be 'toolbox' or 'local'.")
    return {"TOOLBOX_NAME": name, "TOOLBOX_ENDPOINT": endpoint, "NETWORK_TOOLS": network_tools}


def memory_scope(customer_id: str) -> str:
    """One memory partition per participant and customer, so nobody reads another customer's memories."""
    customer_id = customer_id.strip().upper()
    if not CUSTOMER_ID_PATTERN.match(customer_id):
        sys.exit(f"{customer_id!r} is not a customer ID like CUST-1002.")
    return f"{os.environ['WORKSHOP_RESOURCE_PREFIX']}_{customer_id}"


def signed_in_customer() -> str:
    """The customer the support channel has authenticated (simulated with --customer)."""
    customer_id = _parse_args().customer
    if not customer_id:
        sys.exit("This stage simulates a signed-in customer. Add --customer CUST-1002 (any lab customer ID).")
    return customer_id.strip().upper()


class SignedInCustomer(ContextProvider):
    """Tells the agent which customer the channel signed in, on every run."""

    def __init__(self, customer_id: str) -> None:
        super().__init__("signed_in_customer")
        self.customer_id = customer_id

    async def before_run(
        self, *, agent: SupportsAgentRun, session: AgentSession, context: SessionContext, state: dict[str, Any]
    ) -> None:
        context.extend_instructions(
            self.source_id, f"The support channel has signed in the customer {self.customer_id}."
        )


def load_instructions(script_file: str) -> str:
    """Load the instructions that sit next to a stage file: ``main.py`` -> ``main.instructions.md``."""
    return Path(script_file).with_suffix(".instructions.md").read_text(encoding="utf-8").strip()


def agent_name() -> str:
    """Your agent's name in Foundry: unique per participant in a shared project."""
    return f"{load_settings()['WORKSHOP_RESOURCE_PREFIX']}-{AGENT_BASE_NAME}"


def stage_signs_in(module_name: str) -> bool:
    """Stage 11 and later need a signed-in customer: they define ``context_providers(customer_id)``."""
    return hasattr(importlib.import_module(module_name), "context_providers")


def stage_agent(module_name: str, customer_id: str | None = None) -> Agent:
    """Build the agent of a stage file (``main`` or ``solutions.stage_N_...``), as its ``__main__`` block does."""
    module = importlib.import_module(module_name)
    if not stage_signs_in(module_name):
        return build_agent(module.__file__, module.tools())
    if not customer_id:
        raise ValueError(f"{module_name} needs a signed-in customer.")
    middleware = module.middleware(customer_id) if hasattr(module, "middleware") else ()
    return build_agent(module.__file__, module.tools(), module.context_providers(customer_id), middleware=middleware)


def build_agent(
    script_file: str,
    tools: Sequence[Any] = (),
    context_providers: Sequence[Any] = (),
    credential: Any = None,
    middleware: Sequence[Any] = (),
    name: str | None = None,
) -> Agent:
    """Create the SupportBuddy agent for a stage.

    This is a code-first agent: the agent loop (instructions, tool calls, memory) runs in this
    Python process, and only the model runs in Microsoft Foundry. Stage 10 compares it with a
    prompt agent, and Stage 14 runs this same code in Foundry as a hosted agent.
    """
    settings = load_settings()
    effort = os.getenv("SUPPORTBUDDY_REASONING_EFFORT", "medium")
    return Agent(
        name=name or agent_name(),
        client=FoundryChatClient(
            project_endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"],
            model=settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
            credential=credential or AzureCliCredential(),
        ),
        instructions=load_instructions(script_file),
        # "off" for chat models without reasoning support.
        default_options={"reasoning": {"effort": effort}} if effort != "off" else None,
        tools=list(tools) or None,
        context_providers=list(context_providers) or None,
        middleware=[*middleware, HideFinalTextReplay()],
    )


class HideFinalTextReplay(AgentMiddleware):
    """Workaround for DevUI showing every streamed answer twice.

    The Responses client ends each streamed message with the full text marked
    ``merge_mode="replace"``; DevUI renders it as one more delta. This hides that
    update from the stream only. The final response is built from the original
    updates, so nothing the agent produces is lost.
    """

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        streamed: set[str | None] = set()

        def hide_replay(update: AgentResponseUpdate) -> AgentResponseUpdate | None:
            kept = []
            for content in update.contents:
                if content.type == "text":
                    if content.merge_mode == "replace" and update.message_id in streamed:
                        continue
                    if content.text:
                        streamed.add(update.message_id)
                kept.append(content)
            if len(kept) == len(update.contents):
                return None
            trimmed = copy.copy(update)
            trimmed.contents = kept
            return trimmed

        context.stream_update_transforms.append(hide_replay)
        await call_next()


def run(agent: Agent) -> None:
    """Open the agent in DevUI, or answer one prompt in the console when a prompt is given."""
    args = _parse_args()

    if args.prompt:
        # Windows consoles and pipes may default to a legacy code page; model output is Unicode.
        sys.stdout.reconfigure(encoding="utf-8")
        asyncio.run(_answer_once(agent, args.prompt))
    else:
        port = int(os.getenv("DEVUI_PORT", "8090"))
        token = _devui_token()
        print(
            "\n" + "=" * 70
            + f"\nDevUI: http://127.0.0.1:{port}"
            + "\nWhen the browser asks for an authentication token, paste:"
            + f"\n\n    {token}\n"
            + f"\n(Stored in {DEVUI_TOKEN_FILE.name}; the browser remembers it, so you paste it once.)\n"
            + "=" * 70 + "\n",
            flush=True,
        )
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        for provider in _async_providers(agent):
            register_cleanup(agent, functools.partial(provider.__aexit__, None, None, None))
        serve_branded(agent, port, token)


def _async_providers(agent: Agent) -> list[Any]:
    # Agent.open() manages the chat client and MCP tools, not context providers such as Foundry Memory.
    return [p for p in agent.context_providers if isinstance(p, contextlib.AbstractAsyncContextManager)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SupportBuddy lab agent.")
    parser.add_argument("prompt", nargs="?", help="Answer this prompt in the console instead of opening DevUI.")
    parser.add_argument("--customer", help="Stage 11+: the customer signed in to the support channel, e.g. CUST-1002.")
    return parser.parse_args()


def _devui_token() -> str:
    """Reuse one local DevUI token per machine; DevUI only listens on 127.0.0.1."""
    if os.getenv("DEVUI_AUTH_TOKEN"):
        return os.environ["DEVUI_AUTH_TOKEN"]
    if DEVUI_TOKEN_FILE.is_file():
        return DEVUI_TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    DEVUI_TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


@contextlib.asynccontextmanager
async def open_agent(agent: Agent) -> AsyncIterator[Agent]:
    """Open the agent's connections (MCP, toolbox) and async context providers (Foundry Memory)."""
    async with contextlib.AsyncExitStack() as stack:
        for provider in _async_providers(agent):
            await stack.enter_async_context(provider)
        await stack.enter_async_context(agent)
        yield agent


async def _answer_once(agent: Agent, prompt: str) -> None:
    print(f"User:  {prompt}\n")
    async with open_agent(agent):
        session = agent.create_session()
        response = await agent.run(prompt, session=session)
        _print_tool_activity(response)
        # A tool with approval_mode="always_require" stops the run until a person decides.
        while response.user_input_requests:
            decisions = [request.to_function_approval_response(_ask_approval(request)) for request in response.user_input_requests]
            response = await agent.run(Message("user", decisions), session=session)
            _print_tool_activity(response)
    print(f"\nAgent: {response.text}")


def _ask_approval(request: Any) -> bool:
    call = request.function_call
    try:
        answer = input(f"  [approval needed] {call.name} {_compact(call.arguments)} - approve? [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


def _print_tool_activity(response: Any) -> None:
    for message in response.messages:
        for content in message.contents:
            if content.type == "function_call":
                print(f"  [tool call]   {content.name} {_compact(content.arguments)}")
            elif content.type == "mcp_server_tool_call":
                print(f"  [tool call, run by Foundry] {content.tool_name} {_compact(content.arguments)}")
            elif content.type == "function_result":
                print(f"  [tool result] {_compact(content.result)}")


def _compact(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " ..."
