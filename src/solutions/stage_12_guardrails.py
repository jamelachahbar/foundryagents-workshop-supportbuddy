"""Stage 12 - Guardrails.

Instructions ask the model to behave; guardrails make sure it does. This stage adds layers:

1. Platform guardrail (Foundry): a guardrail (RAI policy) is applied to the model deployment
   and, from this stage, to your toolbox (`toolbox.py create --guardrail <id>`). Prompt Shields
   block jailbreak attempts before the model sees them.
2. Code guardrail (this file): function middleware runs before every tool call and refuses any
   call about a customer other than the signed-in one. It is deterministic: whatever the model
   decides, the tool is not called.
3. Proof: run `evaluate.py solutions.stage_12_guardrails` and check the safety cases.

Then run, as a signed-in customer:
    uv run python -m solutions.stage_12_guardrails --customer CUST-1006 ["prompt"]
"""

from collections.abc import Awaitable, Callable

from agent_framework import FunctionInvocationContext, FunctionMiddleware

from lab_agent import build_agent, run, signed_in_customer
from solutions.stage_9_toolbox import tools
from solutions.stage_10_memory import context_providers as memory_context_providers


class SignedInCustomerOnly(FunctionMiddleware):
    """Blocks tool calls whose customer_id is not the customer the channel signed in."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        requested = str(context.arguments.get("customer_id") or self.customer_id).strip().upper()
        if requested != self.customer_id:
            # Do not call the tool. The model receives this result instead.
            context.result = {
                "blocked": True,
                "reason": "Tools may only be used for the signed-in customer.",
            }
            return
        await call_next()


def context_providers(customer_id: str) -> list:
    return memory_context_providers(customer_id)


def middleware(customer_id: str) -> list:
    return [SignedInCustomerOnly(customer_id)]


if __name__ == "__main__":
    customer = signed_in_customer()
    run(build_agent(__file__, tools(), context_providers(customer), middleware=middleware(customer)))
