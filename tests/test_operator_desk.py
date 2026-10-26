"""The operator desk: the human side of the handover (no Azure needed)."""

from starlette.testclient import TestClient

from lab_backend import backend
from operator_desk.server import app

client = TestClient(app)


def _handover():
    return backend.escalate_to_human(
        "No internet", ["identify_customer"], ["LINE-2005 sync=up"], "cause unknown", "check the ONT",
        customer_id="CUST-1005",
    )


def test_desk_lists_handovers_and_approved_actions():
    _handover()
    backend.restart_modem("CUST-1002")
    data = client.get("/api/handovers").json()
    assert [h["handover_id"] for h in data["handovers"]] == ["HO-9001"]
    assert data["handovers"][0]["evidence"] == ["LINE-2005 sync=up"]
    assert [a["customer_id"] for a in data["actions"]] == ["CUST-1002"]


def test_advisor_takes_and_resolves_a_case_and_the_agent_can_read_the_reply():
    _handover()
    taken = client.post("/api/handovers/HO-9001/take", json={"advisor": "Advisor Lea"})
    assert taken.json()["status"] == "in_progress"

    resolved = client.post("/api/handovers/HO-9001/resolve", json={"advisor": "Advisor Lea", "message": "Fixed."})
    assert resolved.json()["status"] == "resolved"
    assert backend.get_handover_status("HO-9001", "CUST-1005")["advisor_message"] == "Fixed."


def test_resolving_needs_a_message_for_the_customer():
    _handover()
    response = client.post("/api/handovers/HO-9001/resolve", json={"advisor": "Advisor Lea", "message": ""})
    assert response.status_code == 400


def test_desk_only_accepts_json_posts():
    # A cross-site HTML form cannot send application/json without a CORS preflight.
    _handover()
    response = client.post("/api/handovers/HO-9001/take", data={"advisor": "x"})
    assert response.status_code == 415


def test_desk_rejects_unknown_cases_and_actions():
    assert client.post("/api/handovers/HO-9999/take", json={"advisor": "x"}).status_code == 404
    assert client.post("/api/handovers/../etc/take", json={"advisor": "x"}).status_code == 404
    assert client.post("/api/handovers/HO-9001/delete", json={"advisor": "x"}).status_code == 404


def test_desk_page_is_served_with_a_strict_content_security_policy():
    response = client.get("/")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "Operator desk" in response.text


def test_desk_script_never_renders_case_data_as_html():
    script = client.get("/desk.js").text
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
