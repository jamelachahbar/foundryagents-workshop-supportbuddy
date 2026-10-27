"""Stage 11 - Memory.

The agent remembers the customer across conversations with Foundry Memory:
preferred language, preferred contact channel and previous problems.
Memories live in the Foundry project, outside the agent's runtime.
Tools are the same as in Stage 9 (Foundry Toolbox + local handover).

One-time setup (from the src folder):  uv run python memory_store.py create
Then run, as a signed-in customer:
    uv run python -m solutions.stage_11_memory --customer CUST-1002 ["prompt"]
or in the contact centre, which signs the customer in for you:
    uv run python -m contact_centre solutions.stage_11_memory
"""

from agent_framework.foundry import FoundryMemoryProvider
from azure.identity.aio import AzureCliCredential

from lab_agent import (
    SignedInCustomer,
    build_agent,
    load_settings,
    memory_scope,
    memory_settings,
    run,
    signed_in_customer,
)
from solutions.stage_9_toolbox import tools


def context_providers(customer_id: str) -> list:
    settings = load_settings()
    memory = memory_settings()
    return [
        SignedInCustomer(customer_id),
        FoundryMemoryProvider(
            project_endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"],
            credential=AzureCliCredential(),
            allow_preview=True,
            memory_store_name=memory["FOUNDRY_MEMORY_STORE_NAME"],
            # From the authenticated channel, never from the chat: no cross-customer recall.
            scope=memory_scope(customer_id),
            # Write right after each turn for the lab; production batches updates with a delay.
            update_delay=0,
        ),
    ]


if __name__ == "__main__":
    run(build_agent(__file__, tools(), context_providers(signed_in_customer())))
