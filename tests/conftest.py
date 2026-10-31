import json

import pytest


@pytest.fixture
def runbook_mcp_result():
    documents = [{"ref_id": 0, "title": "<img src=x onerror=alert(1)>", "content": "private text"}]
    reference = {
        "kind": "reference", "ref_id": 0, "mimeType": "application/json",
        "uri": "https://example.invalid/synthetic-source",
        "sourceData": {"id": "RB-09", "title": documents[0]["title"], "content": documents[0]["content"]},
    }
    return [{"type": "text", "text": json.dumps(documents) + "\n" + json.dumps(reference)}]


@pytest.fixture(autouse=True)
def _isolated_lab_state(tmp_path, monkeypatch):
    # The handover queue is a file shared by every process that runs the tools; tests must never touch the real one.
    monkeypatch.setenv("LAB_STATE_DIR", str(tmp_path / "lab_state"))
    # Tests set their own settings; a participant's src/.env must not leak into them.
    import lab_agent

    monkeypatch.setattr(lab_agent, "load_dotenv", lambda *args, **kwargs: False)
