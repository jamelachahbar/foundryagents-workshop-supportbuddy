"""Runbooks and the Foundry IQ knowledge base definition and MCP tool (no Azure needed)."""

from types import SimpleNamespace

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


@pytest.fixture
def search_client(monkeypatch):
    http_client = httpx.Client
    monkeypatch.setattr(
        knowledge_base,
        "AzureCliCredential",
        lambda: SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test-token")),
    )

    def make(handler):
        monkeypatch.setattr(
            knowledge_base.httpx,
            "Client",
            lambda **kwargs: http_client(**kwargs, transport=httpx.MockTransport(handler)),
        )
        return knowledge_base.SearchClient("https://example.search.windows.net")

    return make


@pytest.mark.parametrize("existing", [False, True])
def test_setup_reports_resource_existence_and_still_updates(search_client, capsys, existing):
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200 if existing else 404, json={})
        if request.method == "POST":
            return httpx.Response(
                200, json={"value": [{"key": f"RB-{n:02d}", "status": True} for n in range(1, 13)]}
            )
        return httpx.Response(200, json={})

    client = search_client(handler)
    try:
        knowledge_base.create(client, {"index": "kb-index", "source": "kb-source", "base": "kb"})
    finally:
        client._http.close()

    output = capsys.readouterr().out
    assert output.count("Already exists; updating..." if existing else "Not found; creating...") == 3
    assert ("Not found; creating..." if existing else "Already exists; updating...") not in output
    assert "Checking search index 'kb-index'..." in output
    assert "Checking knowledge source 'kb-source'..." in output
    assert "Checking knowledge base 'kb'..." in output
    assert "Indexed 12 runbooks into 'kb-index'." in output
    assert "Setup complete: knowledge base 'kb' is ready." in output
    assert 'Next: uv run python knowledge_base.py ask "wifi slow upstairs, cable works"' in output
    assert requests == [
        ("GET", "/indexes/kb-index"),
        ("PUT", "/indexes/kb-index"),
        ("POST", "/indexes/kb-index/docs/index"),
        ("GET", "/knowledgesources/kb-source"),
        ("PUT", "/knowledgesources/kb-source"),
        ("GET", "/knowledgebases/kb"),
        ("PUT", "/knowledgebases/kb"),
    ]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_setup_check_errors_are_not_reported_as_missing(search_client, capsys, status):
    client = search_client(lambda request: httpx.Response(status, text="Request failed"))
    try:
        with pytest.raises(SystemExit, match=f"GET /indexes/kb-index failed: {status}"):
            knowledge_base.create(client, {"index": "kb-index", "source": "kb-source", "base": "kb"})
    finally:
        client._http.close()
    output = capsys.readouterr().out
    assert "Not found" not in output
    assert "Setup complete" not in output


def test_failed_document_upload_does_not_report_setup_complete(search_client, capsys):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(
                200, json={"value": [{"key": "RB-01", "status": False, "errorMessage": "Upload failed"}]}
            )
        return httpx.Response(200, json={})

    client = search_client(handler)
    try:
        with pytest.raises(SystemExit, match="Runbook upload failed:.*RB-01"):
            knowledge_base.create(client, {"index": "kb-index", "source": "kb-source", "base": "kb"})
    finally:
        client._http.close()
    output = capsys.readouterr().out
    assert "Setup complete" not in output
    assert "Indexed 12" not in output


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
