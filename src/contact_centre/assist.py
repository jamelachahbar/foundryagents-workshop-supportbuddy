"""The advisor assist agent: AI help for the human advisor, never for the customer directly.

In Dynamics 365 Contact Center this role is played by Copilot for service representatives
(summaries, suggested replies, ask a question). Here it is a second code-first Agent Framework agent
with the same diagnostic tools as SupportBuddy. Its state-changing tool still needs approval: the advisor's.

The contact centre runs one assist agent per customer and sends each case to the agent of the case's
customer. Code, not the instructions, keeps it on that customer:
  - CaseCustomerOnly refuses any tool call that names another customer, before the tool runs
    (so restart_modem can never run for someone else), and withholds any result about another
    customer, for lookups by e-mail, phone or line ID;
  - while the case has no identified customer, the agent can only identify the customer.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Agent, FunctionInvocationContext, FunctionMiddleware

from lab_agent import agent_name, build_agent
from tools.support_tools import (
    check_equipment,
    get_handover_status,
    get_known_incidents,
    get_line_status,
    identify_customer,
    restart_modem,
)

BLOCKED = {"blocked": True, "reason": "The assist agent may only work on the customer of this case."}


def _customers_in(result: Any) -> set[str]:
    """Customer IDs a tool result is about (local tools return JSON text contents)."""
    found = set()
    for item in result if isinstance(result, list) else [result]:
        data = getattr(item, "text", item)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                continue
        if isinstance(data, dict):
            for record in (data, data.get("customer"), data.get("line")):
                if isinstance(record, dict) and record.get("customer_id"):
                    found.add(str(record["customer_id"]).upper())
    return found


class CaseCustomerOnly(FunctionMiddleware):
    """Keeps every tool call of the assist agent on the customer of the case."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id.upper()

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        requested = str(context.arguments.get("customer_id") or self.customer_id).strip().upper()
        if requested != self.customer_id:
            context.result = BLOCKED
            return
        await call_next()
        if _customers_in(context.result) - {self.customer_id}:
            context.result = BLOCKED


def build_assist_agent(customer_id: str | None) -> Agent:
    """The assist agent for cases of one customer, or for cases whose customer is not identified yet."""
    if customer_id is None:
        return build_agent(__file__, [identify_customer, get_known_incidents], name=f"{agent_name()}-assist")
    return build_agent(
        __file__,
        [identify_customer, get_line_status, get_known_incidents, check_equipment, get_handover_status, restart_modem],
        name=f"{agent_name()}-assist",
        middleware=[CaseCustomerOnly(customer_id)],
    )
