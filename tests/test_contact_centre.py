"""The contact centre: who answers the customer, handover, live chat, approvals and assist (no Azure needed).

SupportBuddy and the assist agent are replaced by scripted AG-UI event streams.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contact_centre.centre import ASSIST_PATH, ContactCentre
from contact_centre.server import create_app
from lab_backend import backend

STATIC = Path(__file__).resolve().parents[1] / "src" / "contact_centre" / "static"


class ScriptedAgents:
    """A fake AG-UI runner: returns the next scripted list of events and records every request."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def __call__(self, path, body):
        self.calls.append((path, json.loads(json.dumps(body))))
        for event in self.scripts.pop(0):
            yield event


def text(message_id, *chunks):
    return [{"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": c} for c in chunks]


def tool(call_id, name, result=None):
    events = [{"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": name}]
    if result is not None:
        events.append({"type": "TOOL_CALL_RESULT", "toolCallId": call_id, "content": json.dumps(result)})
    return events


def snapshot(*messages):
    return [{"type": "MESSAGES_SNAPSHOT", "messages": list(messages)}]


def handover_record():
    return backend.escalate_to_human(
        reported_problem="No internet at all",
        capabilities_checked=["identify_customer", "get_line_status", "get_known_incidents", "check_equipment"],
        evidence=["LINE-2005 up, 941/1000 Mbps"],
        unresolved_issue="Everything is healthy",
        recommended_next_investigation="Check the customer's router",
        customer_id="CUST-1005",
    )


def run(coroutine):
    return asyncio.run(coroutine)


def test_supportbuddy_answers_with_progress_and_streamed_text():
    agents = ScriptedAgents(
        tool("c1", "network___identify_customer", {"found": True})
        + tool("c2", "identify_customer", {"found": True})
        + text("m1", "Your modem ", "is not responding.")
        + snapshot({"id": "x", "role": "user", "content": "hi"})
        + [{"type": "RUN_FINISHED"}]
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "My internet is down, I'm CUST-1002"))

    path, body = agents.calls[0]
    assert path == "/ag-ui/supportbuddy/guest"
    assert body["threadId"] == conversation.id
    assert body["messages"][-1]["content"] == "My internet is down, I'm CUST-1002"
    roles = [(m["role"], m["text"]) for m in conversation.transcript[1:]]
    assert roles == [
        ("customer", "My internet is down, I'm CUST-1002"),
        ("activity", "Looking up your account"),
        ("agent", "Your modem is not responding."),
    ]
    assert conversation.agent_history == [{"id": "x", "role": "user", "content": "hi"}]
    assert conversation.mode == "agent" and not conversation.busy
    # The desk learns who the anonymous customer is, also from MCP-wrapped toolbox results.
    assert conversation.identified is None


def test_desk_learns_the_customer_supportbuddy_identified():
    found = {"found": True, "customer": {"customer_id": "CUST-1003", "name": "Sofia Costa"}}
    agents = ScriptedAgents(
        [{"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "network___identify_customer"},
         {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": json.dumps([{"type": "text", "text": json.dumps(found)}])}]
        + text("m1", "Found you.") + [{"type": "RUN_FINISHED"}]
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Phone +352 600 000 103"))
    assert conversation.identified == {"customer_id": "CUST-1003", "name": "Sofia Costa"}
    assert conversation.summary()["identified"]["customer_id"] == "CUST-1003"


def test_tool_results_are_read_from_local_tools_and_from_the_foundry_toolbox():
    from contact_centre.centre import tool_result

    data = {"found": True, "customer": {"customer_id": "CUST-1001"}}
    toolbox = {"_meta": {"tool_configuration": {"type": "mcp"}}, "content": [{"type": "text", "text": json.dumps(data)}]}
    assert tool_result(json.dumps(data)) == data
    assert tool_result(json.dumps(toolbox)) == data
    assert tool_result("not json") is None


def test_handover_moves_the_conversation_to_a_human_advisor_and_back():
    record = handover_record()
    agents = ScriptedAgents(
        tool("c1", "escalate_to_human", record) + text("m1", "I handed this over.") + [{"type": "RUN_FINISHED"}],
        text("m2", "Welcome back.") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Nothing loads"))

    assert conversation.mode == "waiting"
    assert conversation.handover["handover_id"] == record["handover_id"]
    assert record["handover_id"] in conversation.transcript[-1]["text"]

    # While a human owns the conversation, SupportBuddy stays silent.
    run(centre.customer_message(conversation, "Hello?"))
    assert len(agents.calls) == 1
    with pytest.raises(ValueError):
        centre.advisor_message(conversation, "Lea", "Not taken yet")

    centre.take(conversation, "Lea")
    assert conversation.mode == "advisor" and conversation.advisor == "Lea"
    assert backend.recorded_handovers()[0]["status"] == "in_progress"

    centre.advisor_message(conversation, "Lea", "Hi, I'm Lea.")
    assert conversation.transcript[-1] == {**conversation.transcript[-1], "role": "advisor", "author": "Lea"}
    assert conversation.agent_history[-1]["content"] == "(Human advisor Lea) Hi, I'm Lea."

    centre.hand_back(conversation, "Lea")
    run(centre.customer_message(conversation, "Thanks"))
    assert len(agents.calls) == 2
    assert agents.calls[1][1]["messages"][-1]["content"] == "Thanks"

    centre.take(conversation, "Lea")
    centre.advisor_message(conversation, "Lea", "Your router needed a reset.")
    centre.close(conversation, "Lea")
    assert conversation.mode == "closed"
    stored = backend.recorded_handovers()[0]
    assert stored["status"] == "resolved"
    assert stored["advisor_message"] == "Your router needed a reset."
    with pytest.raises(ValueError):
        run(centre.customer_message(conversation, "Still there?"))


def test_customer_approval_resumes_the_run():
    interrupt = {
        "id": "approval-1",
        "metadata": {"agent_framework": {"function_call": {"name": "restart_modem", "arguments": {"customer_id": "CUST-1002"}}}},
    }
    agents = ScriptedAgents(
        tool("c1", "restart_modem") + [{"type": "RUN_FINISHED", "outcome": {"type": "interrupt", "interrupts": [interrupt]}}],
        text("m1", "Restart sent.") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Please restart my modem"))

    approval = conversation.transcript[-1]
    assert approval["role"] == "approval" and approval["status"] == "pending"
    assert approval["tool"] == "restart_modem" and approval["arguments"] == {"customer_id": "CUST-1002"}

    run(centre.customer_approval(conversation, "approval-1", True))
    assert approval["status"] == "approved"
    assert agents.calls[1][1]["resume"] == [{"interruptId": "approval-1", "status": "resolved", "payload": {"approved": True}}]
    assert conversation.transcript[-1]["text"] == "Restart sent."
    with pytest.raises(ValueError):
        run(centre.customer_approval(conversation, "approval-1", False))
    with pytest.raises(LookupError):
        run(centre.customer_approval(conversation, "unknown", True))


def test_assist_works_for_the_advisor_only():
    record = handover_record()
    interrupt = {"id": "a-1", "metadata": {"agent_framework": {"function_call": {"name": "restart_modem", "arguments": {}}}}}
    agents = ScriptedAgents(
        tool("c1", "escalate_to_human", record) + [{"type": "RUN_FINISHED"}],
        text("m1", "Suggested reply:\nHello") + [{"type": "RUN_FINISHED"}],
        [{"type": "RUN_FINISHED", "outcome": {"type": "interrupt", "interrupts": [interrupt]}}],
        text("m2", "Done") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Nothing loads"))
    customer_messages = len(conversation.transcript)

    run(centre.assist(conversation, "Lea", "Suggest a reply."))
    path, body = agents.calls[1]
    assert path == ASSIST_PATH
    assert body["threadId"] == f"{conversation.id}-assist"
    assert record["handover_id"] in body["messages"][-1]["content"]
    assert "Nothing loads" in body["messages"][-1]["content"]
    assert [m["role"] for m in conversation.assist_transcript] == ["advisor", "agent"]
    assert len(conversation.transcript) == customer_messages  # the customer sees none of the assist thread

    run(centre.assist(conversation, "Lea", "Restart the modem."))
    assert conversation.assist_transcript[-1]["role"] == "approval"
    run(centre.assist_approval(conversation, "Lea", "a-1", True))
    assert agents.calls[3][1]["resume"][0]["payload"] == {"approved": True}
    # The customer is told about an action on their service, and who took it; SupportBuddy learns it is done.
    assert conversation.transcript[-1]["text"].startswith("Lea restarted your modem remotely.")
    assert "do not run it again" in conversation.agent_history[-1]["content"]


def test_signed_in_conversations_use_one_agent_per_customer():
    agents = ScriptedAgents(text("m1", "Hi") + [{"type": "RUN_FINISHED"}])
    centre = ContactCentre(agents, signs_in=True, agent_label="test")
    with pytest.raises(ValueError):
        centre.start(None)
    conversation = centre.start("CUST-1002")
    run(centre.customer_message(conversation, "Hello"))
    assert agents.calls[0][0] == "/ag-ui/supportbuddy/CUST-1002"


def test_run_errors_are_shown_without_internal_details():
    agents = ScriptedAgents([{"type": "RUN_ERROR", "message": "boom"}])
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Hello"))
    last = conversation.transcript[-1]
    assert last["role"] == "system" and "boom" not in last["text"]


# ----- HTTP API ---------------------------------------------------------------------------------


@pytest.fixture
def api():
    agents = ScriptedAgents(*[text("m1", "Hello!") + [{"type": "RUN_FINISHED"}] for _ in range(5)])
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    customers = [{"customer_id": "CUST-1002", "name": "Luc Martin"}]
    with TestClient(create_app(centre, {}, customers=customers)) as client:
        yield client, centre


def test_customer_conversation_over_http(api):
    client, centre = api
    assert client.get("/api/config").json() == {
        "agent": "test", "signs_in": False, "customers": [{"customer_id": "CUST-1002", "name": "Luc Martin"}],
    }
    conversation = client.post("/api/conversations", json={}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"text": "Hi"})
    assert response.status_code == 202
    stored = centre.store.get(conversation["id"])
    for _ in range(50):
        if any(m["role"] == "agent" for m in stored.transcript):
            break
        time.sleep(0.05)
    assert stored.transcript[-1]["text"] == "Hello!"


def test_api_rejects_bad_requests(api):
    client, _ = api
    conversation = client.post("/api/conversations", json={}).json()
    cid = conversation["id"]
    assert client.post("/api/conversations", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/conversations", json={"customer_id": "CUST-9999"}).status_code == 400
    assert client.post("/api/conversations/not-an-id/messages", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": ""}).status_code == 400
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "x" * 2001}).status_code == 400
    assert client.post(f"/api/conversations/{cid}/approvals", json={"interrupt_id": "x", "approved": "yes"}).status_code == 400
    assert client.post(f"/api/conversations/{cid}/approvals", json={"interrupt_id": "x", "approved": True}).status_code == 409
    assert client.post(f"/api/desk/conversations/{cid}/take", json={}).status_code == 400
    assert client.post(f"/api/desk/conversations/{cid}/messages", json={"advisor": "Lea", "text": "hi"}).status_code == 409
    assert client.post(f"/api/desk/conversations/{cid}/delete", json={"advisor": "Lea"}).status_code == 404
    assert client.post(f"/api/desk/conversations/{cid}/take", json={"advisor": "Lea"}).status_code == 200
    assert client.post(f"/api/desk/conversations/{cid}/messages", json={"advisor": "Lea", "text": "hi"}).status_code == 200


@pytest.mark.parametrize("page", ["/", "/chat", "/desk", "/chat.js", "/desk.js", "/common.js", "/centre.css", "/icon.svg"])
def test_pages_are_served_with_security_headers(api, page):
    client, _ = api
    response = client.get(page)
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("script", ["chat.js", "desk.js", "common.js"])
def test_conversation_text_is_never_parsed_as_html(script):
    source = (STATIC / script).read_text(encoding="utf-8")
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert unsafe not in source
