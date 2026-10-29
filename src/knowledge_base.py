"""Create and test the Foundry IQ knowledge base of synthetic troubleshooting runbooks (Stage 8).

Run from the src folder:
    uv run python knowledge_base.py create                       # instructor once per shared environment
    uv run python knowledge_base.py ask "wifi slow upstairs"     # query it like the agent will

A Foundry IQ knowledge base lives in Azure AI Search: an index holds the runbooks, a knowledge
source points at the index, and the knowledge base exposes retrieval as an MCP endpoint that the
agent calls. Every runbook is fictional training content, not a real operator procedure.
Participants in a shared workshop use ask only. Personal-environment owners run create themselves.
Rerunning create updates the shared resources and uploads the runbooks again; it makes no personal copy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from azure.identity import AzureCliCredential

from lab_agent import knowledge_settings

API_VERSION = "2026-08-01-preview"  # same version as the knowledge base MCP endpoint
SEARCH_SCOPE = "https://search.azure.com/.default"
RUNBOOK_DIR = Path(__file__).parent / "knowledge" / "runbooks"
SEMANTIC_CONFIG = "runbooks-semantic"


def load_runbooks(directory: Path = RUNBOOK_DIR) -> list[dict[str, str]]:
    """Read the Markdown runbooks and their front matter into search documents."""
    documents = []
    for path in sorted(directory.glob("RB-*.md")):
        _, front_matter, body = path.read_text(encoding="utf-8").split("---", 2)
        meta = {}
        for line in front_matter.strip().splitlines():
            key, value = line.split(":", 1)
            value = value.strip()
            meta[key.strip()] = json.loads(value) if value.startswith("[") else value.strip('"')
        documents.append(
            {
                "id": meta["id"],
                "title": f"{meta['id']} {meta['title']}",
                "symptoms": ", ".join(meta.get("symptoms", [])),
                "content": "\n".join(
                    filter(None, [body.strip(), f"Escalate only if, after the customer has tried these steps: {meta['escalate_if']}" if meta.get("escalate_if") else ""])
                ),
            }
        )
    return documents


def definitions(names: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Index, knowledge source and knowledge base definitions."""
    index = {
        "name": names["index"],
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "symptoms", "type": "Edm.String", "searchable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
        ],
        "semantic": {
            "defaultConfiguration": SEMANTIC_CONFIG,
            "configurations": [
                {
                    "name": SEMANTIC_CONFIG,
                    "prioritizedFields": {
                        "titleField": {"fieldName": "title"},
                        "prioritizedContentFields": [{"fieldName": "content"}],
                        "prioritizedKeywordsFields": [{"fieldName": "symptoms"}],
                    },
                }
            ],
        },
    }
    source = {
        "name": names["source"],
        "kind": "searchIndex",
        "description": "Synthetic connectivity troubleshooting runbooks (lab content).",
        "searchIndexParameters": {
            "searchIndexName": names["index"],
            "semanticConfigurationName": SEMANTIC_CONFIG,
            "sourceDataFields": [{"name": "id"}, {"name": "title"}, {"name": "content"}],
        },
    }
    # No attached chat model: minimal skips LLM query planning; extractive output skips answer synthesis.
    # Microsoft-hosted semantic ranking still runs. This is a workshop choice, not a universal recommendation.
    base = {
        "name": names["base"],
        "description": "SupportBuddy lab runbooks. Synthetic content for training only.",
        "knowledgeSources": [{"name": names["source"]}],
        "outputMode": "extractiveData",
        "retrievalReasoningEffort": {"kind": "minimal"},
    }
    return index, source, base


class SearchClient:
    def __init__(self, endpoint: str) -> None:
        token = AzureCliCredential().get_token(SEARCH_SCOPE).token
        self._http = httpx.Client(
            base_url=endpoint.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            params={"api-version": API_VERSION},
            timeout=60,
        )

    def exists(self, path: str) -> bool:
        response = self._http.get(path)
        if response.status_code == 404:
            return False
        if response.is_error:
            sys.exit(f"GET {path} failed: {response.status_code} {response.text}")
        return True

    def send(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._http.request(method, path, json=body)
        if response.is_error:
            sys.exit(f"{method} {path} failed: {response.status_code} {response.text}")
        return response.json() if response.content else {}


def create(client: SearchClient, names: dict[str, str]) -> None:
    index, source, base = definitions(names)
    runbooks = load_runbooks()
    for label, path, definition in (
        ("Search index", f"/indexes/{names['index']}", index),
        ("Knowledge source", f"/knowledgesources/{names['source']}", source),
        ("Knowledge base", f"/knowledgebases/{names['base']}", base),
    ):
        print(f"Checking {label.lower()} '{definition['name']}'...", flush=True)
        if client.exists(path):
            print("  Already exists; updating...", flush=True)
        else:
            print("  Not found; creating...", flush=True)
        client.send("PUT", path, definition)
        print("  Ready.", flush=True)
        if label == "Search index":
            print(f"Uploading {len(runbooks)} synthetic runbooks...", flush=True)
            result = client.send(
                "POST",
                f"/indexes/{names['index']}/docs/index",
                {"value": [{"@search.action": "mergeOrUpload", **doc} for doc in runbooks]},
            )
            failures = [item for item in result["value"] if not item["status"]]
            if failures:
                sys.exit(f"Runbook upload failed: {json.dumps(failures)}")
            print(f"Indexed {len(runbooks)} runbooks into '{names['index']}'.", flush=True)
    print(f"Setup complete: knowledge base '{names['base']}' is ready.")
    print('Next: uv run python knowledge_base.py ask "wifi slow upstairs, cable works"')


def ask(client: SearchClient, names: dict[str, str], query: str) -> None:
    result = client.send(
        "POST",
        f"/knowledgebases/{names['base']}/retrieve",
        {"intents": [{"type": "semantic", "search": query}]},
    )
    for item in result.get("response", []):
        for content in item.get("content", []):
            for ref in json.loads(content.get("text", "[]")):
                print(f"- {ref.get('title')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the SupportBuddy Foundry IQ knowledge base.")
    parser.add_argument("action", choices=["create", "ask"])
    parser.add_argument("query", nargs="?", help="Question for 'ask'.")
    args = parser.parse_args()

    settings = knowledge_settings()
    names = {
        "base": settings["FOUNDRY_KNOWLEDGE_BASE_NAME"],
        "source": f"{settings['FOUNDRY_KNOWLEDGE_BASE_NAME']}-source",
        "index": f"{settings['FOUNDRY_KNOWLEDGE_BASE_NAME']}-index",
    }
    client = SearchClient(settings["AZURE_AI_SEARCH_ENDPOINT"])
    if args.action == "create":
        create(client, names)
    else:
        if not args.query:
            parser.error('ask needs a question, for example "wifi slow upstairs"')
        ask(client, names, args.query)
    return 0


if __name__ == "__main__":
    sys.exit(main())
