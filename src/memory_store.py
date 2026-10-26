"""Create and inspect the Foundry Memory store used in Stage 9.

Run from the src folder:
    uv run python memory_store.py create              # once per workshop (idempotent)
    uv run python memory_store.py show CUST-1002      # what SupportBuddy remembers about a customer
    uv run python memory_store.py forget CUST-1002    # delete everything remembered about a customer

Foundry Memory is a preview feature. Memories live in the Foundry project, outside the agent's runtime.
"""

from __future__ import annotations

import argparse
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MemoryStoreDefaultDefinition, MemoryStoreDefaultOptions
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureCliCredential

from lab_agent import load_settings, memory_scope, memory_settings

# Tells the extractor what is worth remembering for this agent, and what must never be kept.
WHAT_TO_REMEMBER = (
    "Keep only: the customer's preferred language, preferred contact channel (SMS, e-mail or phone call), "
    "and short summaries of previous connectivity problems with their outcome (cause found, advice given, "
    "or lab handover reference HO-xxxx). Do not keep names, addresses, phone numbers, e-mail addresses, "
    "payment or contract details, credentials, or any other personal data."
)


def create(project: AIProjectClient, store: str, chat_model: str, embedding_model: str) -> None:
    try:
        existing = project.beta.memory_stores.get(store)
        print(f"Memory store '{existing.name}' already exists.")
        return
    except ResourceNotFoundError:
        pass
    created = project.beta.memory_stores.create(
        name=store,
        description="SupportBuddy lab: customer preferences and previous connectivity issues (synthetic data only).",
        definition=MemoryStoreDefaultDefinition(
            chat_model=chat_model,
            embedding_model=embedding_model,
            options=MemoryStoreDefaultOptions(
                user_profile_enabled=True,
                user_profile_details=WHAT_TO_REMEMBER,
                chat_summary_enabled=True,
            ),
        ),
    )
    print(f"Created memory store '{created.name}'.")


def show(project: AIProjectClient, store: str, scope: str) -> None:
    items = list(project.beta.memory_stores.list_memories(store, scope=scope))
    print(f"{len(items)} memories in scope '{scope}':")
    for item in items:
        print(f"  [{item.kind}] {item.content}")


def forget(project: AIProjectClient, store: str, scope: str) -> None:
    project.beta.memory_stores.delete_scope(name=store, scope=scope)
    print(f"Deleted all memories in scope '{scope}'.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the SupportBuddy Foundry Memory store.")
    parser.add_argument("action", choices=["create", "show", "forget"])
    parser.add_argument("customer_id", nargs="?", help="Customer ID for show/forget, for example CUST-1002.")
    args = parser.parse_args()

    settings = load_settings()
    memory = memory_settings()
    if args.action != "create" and not args.customer_id:
        parser.error(f"{args.action} needs a customer ID, for example CUST-1002")

    with AIProjectClient(
        endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=AzureCliCredential(), allow_preview=True
    ) as project:
        if args.action == "create":
            create(
                project,
                memory["FOUNDRY_MEMORY_STORE_NAME"],
                settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                memory["AZURE_AI_EMBEDDING_DEPLOYMENT_NAME"],
            )
        elif args.action == "show":
            show(project, memory["FOUNDRY_MEMORY_STORE_NAME"], memory_scope(args.customer_id))
        else:
            forget(project, memory["FOUNDRY_MEMORY_STORE_NAME"], memory_scope(args.customer_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
