"""Check that your laptop is ready for the lab before the workshop day.

Run from the src folder:  uv run python preflight.py
"""

from __future__ import annotations

import asyncio
import sys

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential

from lab_agent import load_settings


def check(label: str, ok: bool, hint: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n       -> {hint}"))
    return ok


async def model_answers(settings: dict[str, str], credential: AzureCliCredential) -> bool:
    agent = Agent(
        client=FoundryChatClient(
            project_endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"],
            model=settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
            credential=credential,
        ),
        instructions="Reply with the single word OK.",
    )
    response = await agent.run("Are you there?")
    return bool(response.text.strip())


def main() -> int:
    results = [check("Python 3.12 or later", sys.version_info[:2] >= (3, 12), "Install Python 3.12 and run uv sync.")]

    settings = load_settings()  # exits with a clear message if .env is missing or invalid
    results.append(check(f"src/.env settings (prefix {settings['WORKSHOP_RESOURCE_PREFIX']})", True))

    credential = AzureCliCredential()
    try:
        credential.get_token("https://ai.azure.com/.default")
        signed_in = True
    except Exception as error:  # noqa: BLE001 - report any sign-in problem to the participant
        signed_in = False
        print(f"       {type(error).__name__}: {error}")
    results.append(check("Azure CLI sign-in (keyless)", signed_in, "Run: az login --tenant <your-tenant>"))

    if signed_in:
        try:
            answered = asyncio.run(model_answers(settings, credential))
        except Exception as error:  # noqa: BLE001
            answered = False
            print(f"       {type(error).__name__}: {error}")
        results.append(
            check(
                "Foundry project reachable and model deployment answers",
                answered,
                "Check the endpoint and deployment name in .env, and that you have the Foundry User role on the project.",
            )
        )

    print("\nReady for the lab." if all(results) else "\nFix the items marked FAIL, then run preflight again.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
