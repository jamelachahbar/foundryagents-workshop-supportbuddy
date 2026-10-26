"""Runbooks and the Foundry IQ knowledge base definition and MCP tool (no Azure needed)."""

import httpx
import pytest

import knowledge_base
from tools import knowledge_tools


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_all_twelve_synthetic_runbooks_load_with_id_title_and_steps():
    runbooks = knowledge_base.load_runbooks()

    assert [r["id"] for r in runbooks] == [f"RB-{n:02d}" for n in range(1, 13)]
    for runbook in runbooks:
        assert runbook["title"].startswith(runbook["id"] + " ")
        assert runbook["symptoms"]
        assert "1. " in runbook["content"]


def test_runbook_files_are_marked_synthetic():
    for path in knowledge_base.RUNBOOK_DIR.glob("RB-*.md"):
        assert "synthetic: true" in path.read_text(encoding="utf-8"), path.name


def test_knowledge_base_is_ready_for_agents_over_mcp():
    _, source, base = knowledge_base.definitions({"base": "kb", "source": "kb-source", "index": "kb-index"})
    assert source["searchIndexParameters"]["searchIndexName"] == "kb-index"
    assert base["knowledgeSources"] == [{"name": "kb-source"}]
    # No model: the agent's own model writes the answer from extractive runbook text.
    assert "models" not in base
    assert base["outputMode"] == "extractiveData"
    assert base["retrievalReasoningEffort"] == {"kind": "minimal"}


def test_escalation_condition_only_applies_after_the_customer_tried_the_steps():
    modem = next(r for r in knowledge_base.load_runbooks() if r["id"] == "RB-02")
    assert modem["content"].endswith(
        "Escalate only if, after the customer has tried these steps: the modem is still unreachable after one restart"
    )


def test_runbook_tool_is_the_knowledge_base_mcp_endpoint():
    tool = knowledge_tools.runbook_knowledge_base("https://example.search.windows.net/", "kb")
    assert tool.name == "runbooks"
    assert tool.url == "https://example.search.windows.net/knowledgebases/kb/mcp?api-version=2026-08-01-preview"
    assert tool.allowed_tools == {"knowledge_base_retrieve"} or list(tool.allowed_tools) == ["knowledge_base_retrieve"]


@pytest.mark.anyio
async def test_every_knowledge_base_request_carries_an_entra_token():
    async def token():
        return "test-token"

    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200)

    auth = knowledge_tools.EntraBearerAuth(token)
    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(handler)) as http:
        await http.post("https://example.search.windows.net/knowledgebases/kb/mcp")
    assert seen["auth"] == "Bearer test-token"
