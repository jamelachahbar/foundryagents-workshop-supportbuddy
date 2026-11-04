"""Check that your laptop is ready for the lab before the workshop day.

Run from the src folder:  uv run python preflight.py
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential

from lab_agent import load_settings


def check(label: str, ok: bool, hint: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n       -> {hint}"))
    return ok


def check_azd() -> None:
    """Report Stage 14 tooling without affecting day-1 readiness."""
    version_ok = False
    version_hint = "Install or update Azure Developer CLI to 1.27.1 or later before Stage 14."
    try:
        result = subprocess.run(["azd", "version"], capture_output=True, text=True, check=True, timeout=10)
        match = re.search(r"\bazd version (\d+)\.(\d+)\.(\d+)(-[\w.-]+)?", result.stdout)
        if match:
            version = tuple(int(part) for part in match.group(1, 2, 3))
            version_ok = version > (1, 27, 1) or (version == (1, 27, 1) and not match.group(4))
            version_hint += f" Detected {match.group(0)}."
        else:
            version_hint += f" Unrecognized version output: {result.stdout.strip()!r}."
    except (OSError, subprocess.SubprocessError) as error:
        version_hint += f" {error}"

    extension_ok = False
    extension_hint = "Before Stage 14, run: azd ext install microsoft.foundry"
    try:
        result = subprocess.run(
            ["azd", "ext", "list", "--installed", "--output", "json"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        extensions = json.loads(result.stdout)
        if not isinstance(extensions, list) or not all(isinstance(item, dict) for item in extensions):
            raise ValueError("Expected an extension list in azd JSON output.")
        extension_ok = any(
            item.get("id") == "microsoft.foundry"
            and isinstance(item.get("installedVersion"), str)
            and item["installedVersion"].strip()
            for item in extensions
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        extension_hint += f" Could not verify installed extensions: {error}"

    for label, ok, hint in (
        ("Azure Developer CLI >= 1.27.1 (Stage 14)", version_ok, version_hint),
        ("microsoft.foundry extension installed (Stage 14)", extension_ok, extension_hint),
    ):
        print(f"[{'OK' if ok else 'WARN'}] {label}" + ("" if ok else f"\n       -> {hint}"))


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
    check_azd()

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
